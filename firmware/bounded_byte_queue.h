#pragma once

#include <array>
#include <cstddef>
#include <cstdint>

namespace acurite {

// Keeps unsent UART bytes in order across partial TCP writes. A full queue
// rejects new bytes; it must never overwrite the beginning of a transcript.
template<size_t Capacity> class BoundedByteQueue {
 public:
  static_assert(Capacity > 0, "Queue capacity must be positive");

  bool push(uint8_t byte) {
    if (size_ == Capacity) return false;
    bytes_[(head_ + size_) % Capacity] = byte;
    ++size_;
    return true;
  }

  const uint8_t *data() const { return bytes_.data() + head_; }
  size_t size() const { return size_; }
  size_t contiguous_size() const {
    const size_t to_end = Capacity - head_;
    return size_ < to_end ? size_ : to_end;
  }

  void consume(size_t count) {
    if (count > size_) count = size_;
    head_ = (head_ + count) % Capacity;
    size_ -= count;
  }

  void clear() { head_ = size_ = 0; }

 private:
  std::array<uint8_t, Capacity> bytes_{};
  size_t head_{0};
  size_t size_{0};
};

}  // namespace acurite
