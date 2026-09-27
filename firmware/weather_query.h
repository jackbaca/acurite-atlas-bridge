#pragma once

#include <cmath>
#include <cstdlib>
#include <initializer_list>
#include <string>

namespace acurite {

inline int hex_digit(char c) {
  if (c >= '0' && c <= '9') return c - '0';
  if (c >= 'a' && c <= 'f') return c - 'a' + 10;
  if (c >= 'A' && c <= 'F') return c - 'A' + 10;
  return -1;
}

inline std::string form_decode(const std::string &value) {
  std::string decoded;
  decoded.reserve(value.size());
  for (size_t i = 0; i < value.size(); ++i) {
    if (value[i] == '+') {
      decoded += ' ';
    } else if (value[i] == '%' && i + 2 < value.size() &&
               hex_digit(value[i + 1]) >= 0 && hex_digit(value[i + 2]) >= 0) {
      decoded += static_cast<char>((hex_digit(value[i + 1]) << 4) | hex_digit(value[i + 2]));
      i += 2;
    } else {
      decoded += value[i];
    }
  }
  return decoded;
}

// Match complete form keys. For example, indoortempf is never tempf, and
// deviceid is never id. Splitting happens before percent decoding values.
inline std::string form_value(const std::string &query, const std::string &key) {
  size_t start = 0;
  while (start < query.size()) {
    const size_t end = query.find('&', start);
    const size_t field_end = end == std::string::npos ? query.size() : end;
    const size_t equal = query.find('=', start);
    if (equal != std::string::npos && equal < field_end &&
        query.compare(start, equal - start, key) == 0) {
      return form_decode(query.substr(equal + 1, field_end - equal - 1));
    }
    if (end == std::string::npos) break;
    start = end + 1;
  }
  return {};
}

inline bool finite_float(const std::string &value, float &result) {
  if (value.empty() || value.find('\0') != std::string::npos) return false;
  char *end = nullptr;
  const float parsed = std::strtof(value.c_str(), &end);
  if (end == value.c_str() || end != value.c_str() + value.size() || !std::isfinite(parsed)) return false;
  result = parsed;
  return true;
}

inline bool has_weather_value(const std::string &query) {
  for (const char *key : {"tempf", "humidity", "baromin", "windspeedmph", "windgustmph",
                          "winddir", "winddirdeg", "dailyrainin", "rainin", "dewptf", "dewpt",
                          "uvindex", "windgustdir", "windspeedavgmph", "lightintensity",
                          "measured_light_seconds", "feelslike", "heatindex", "windchill", "rssi"}) {
    float value;
    if (finite_float(form_value(query, key), value)) return true;
  }
  return false;
}

}  // namespace acurite
