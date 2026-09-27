#pragma once

#include <ESP8266WiFi.h>
#include "esphome/components/uart/uart_component.h"
#include "esphome/core/log.h"
#include "bounded_byte_queue.h"

namespace acurite {

// Diagnostic transport only: no AT responses, weather parsing, or TCP framing.
// A host connection has exclusive UART ownership. Bytes captured without a
// host are discarded rather than replayed as if they were current commands.
class UartTcpBridge {
 public:
  explicit UartTcpBridge(esphome::uart::UARTComponent *uart)
      : uart_(uart), server_(6638) {}

  // Returns true only when the caller may run the old local emulator.
  bool poll(bool legacy_requested) {
    if (!started_ && WiFi.status() == WL_CONNECTED) {
      server_.begin(6638, 1);
      server_.setNoDelay(true);
      started_ = true;
      ESP_LOGI("acurite.bridge", "Raw UART TCP bridge listening on port 6638");
    }

    if (attached_ && !client_.connected()) release_client_(false);
    if (started_ && server_.hasClient()) {
      WiFiClient incoming = server_.accept();
      if (attached_) {
        incoming.stop(1);
        ++rejected_clients_;
        ESP_LOGW("acurite.bridge", "Rejected second UART controller");
      } else {
        client_ = incoming;
        client_.setNoDelay(true);
        client_.setSync(false);
        client_.setTimeout(1);
        client_.keepAlive(30, 5, 3);
        pending_.clear();
        last_progress_ms_ = millis();
        attached_ = true;
        ESP_LOGI("acurite.bridge", "Host acquired exclusive UART control");
      }
    }

    if (!attached_) {
      if (legacy_requested) return true;
      discard_uart_();
      return false;
    }

    flush_pending_();
    // Drain quickly enough for 115200 8N1. The ESPHome RX buffer separately
    // absorbs short scheduler stalls. No raw bytes are written to logs.
    for (size_t count = 0; count < 1024 && uart_->available(); ++count) {
      uint8_t byte;
      if (!uart_->read_byte(&byte)) break;
      ++uart_rx_bytes_;
      if (!pending_.push(byte)) {
        ++discarded_bytes_;
        ++overflows_;
        ESP_LOGE("acurite.bridge", "UART transcript overflow; closing host connection");
        release_client_(true);
        discard_uart_();
        return false;
      }
    }
    flush_pending_();

    // Bound each UART write to 64 bytes (~5.6 ms at 115200 even when the
    // hardware FIFO is full). TCP provides backpressure for larger replies.
    const int available = client_.available();
    if (available > 0) {
      uint8_t outgoing[64];
      const size_t count = available < 64 ? static_cast<size_t>(available) : 64;
      const int read = client_.read(outgoing, count);
      if (read > 0) {
        uart_->write_array(outgoing, static_cast<size_t>(read));
        uart_tx_bytes_ += static_cast<uint32_t>(read);
      }
    }

    if (pending_.size() && static_cast<uint32_t>(millis() - last_progress_ms_) > 2000) {
      ++stalls_;
      ESP_LOGE("acurite.bridge", "Host stopped consuming UART; closing connection");
      release_client_(true);
    }
    return false;
  }

  bool host_connected() const { return attached_; }
  uint32_t uart_rx_bytes() const { return uart_rx_bytes_; }
  uint32_t uart_tx_bytes() const { return uart_tx_bytes_; }
  uint32_t discarded_bytes() const { return discarded_bytes_; }
  uint32_t overflows() const { return overflows_; }
  uint32_t stalls() const { return stalls_; }
  uint32_t rejected_clients() const { return rejected_clients_; }

 private:
  void flush_pending_() {
    if (!pending_.size()) {
      last_progress_ms_ = millis();
      return;
    }
    const int available = client_.availableForWrite();
    if (available <= 0) return;
    size_t count = pending_.contiguous_size();
    if (count > static_cast<size_t>(available)) count = static_cast<size_t>(available);
    if (count > 1024) count = 1024;
    const size_t written = client_.write(pending_.data(), count);
    pending_.consume(written);
    if (written) last_progress_ms_ = millis();
  }

  void discard_uart_() {
    for (size_t count = 0; count < 1024 && uart_->available(); ++count) {
      uint8_t byte;
      if (!uart_->read_byte(&byte)) break;
      ++discarded_bytes_;
    }
  }

  void release_client_(bool fault) {
    discarded_bytes_ += static_cast<uint32_t>(pending_.size());
    pending_.clear();
    // ESP8266 stop() without an explicit timeout may wait 300 ms. Keep the
    // scheduler responsive so ESPHome API/OTA remain available during faults.
    client_.stop(1);
    client_ = WiFiClient();
    attached_ = false;
    ESP_LOGI("acurite.bridge", "Host released UART control%s", fault ? " after transport fault" : "");
  }

  esphome::uart::UARTComponent *uart_;
  WiFiServer server_;
  WiFiClient client_;
  BoundedByteQueue<4096> pending_;
  bool started_{false};
  bool attached_{false};
  uint32_t last_progress_ms_{0};
  uint32_t uart_rx_bytes_{0};
  uint32_t uart_tx_bytes_{0};
  uint32_t discarded_bytes_{0};
  uint32_t overflows_{0};
  uint32_t stalls_{0};
  uint32_t rejected_clients_{0};
};

}  // namespace acurite
