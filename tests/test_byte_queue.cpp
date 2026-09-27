#include "../firmware/bounded_byte_queue.h"
#include <cassert>
#include <cstdint>
#include <vector>

int main() {
  acurite::BoundedByteQueue<8> queue;
  assert(queue.size() == 0);
  for (uint8_t byte = 0; byte < 8; ++byte) assert(queue.push(byte));
  assert(!queue.push(99));  // Backpressure must not overwrite older bytes.
  for (uint8_t byte = 0; byte < 3; ++byte) assert(queue.data()[byte] == byte);
  queue.consume(3);  // Simulate a short TCP write, then wrap the queue.
  for (uint8_t byte = 8; byte < 11; ++byte) assert(queue.push(byte));
  std::vector<uint8_t> received;
  while (queue.size()) {
    const size_t available = queue.contiguous_size();
    const size_t written = available < 2 ? available : 2;
    received.insert(received.end(), queue.data(), queue.data() + written);
    queue.consume(written);
  }
  assert((received == std::vector<uint8_t>{3, 4, 5, 6, 7, 8, 9, 10}));
  assert(queue.push(11));
  queue.clear();  // New clients must not receive a previous transcript tail.
  assert(queue.size() == 0);
  assert(queue.push(12));
  assert(queue.data()[0] == 12);
}
