#include "../firmware/weather_query.h"
#include <cassert>
#include <cmath>
#include <string>

int main() {
  const std::string query =
      "indoortempf=71&deviceid=wrong&tempf=94.2&id=station&humidity=31&baromin=29.92"
      "&winddirdeg=135&rainin=0.04&dewpt=61&dateutc=2026-09-26+19%3A00%3A00";
  assert(acurite::form_value(query, "tempf") == "94.2");
  assert(acurite::form_value(query, "id") == "station");
  assert(acurite::form_value(query, "winddir").empty());
  assert(acurite::form_value(query, "winddirdeg") == "135");
  assert(acurite::form_value(query, "dateutc") == "2026-09-26 19:00:00");
  assert(acurite::form_value("x=hello%26tempf%3D900", "tempf").empty());
  assert(acurite::form_value("x=&tempf=90&tempf=91", "tempf") == "90");
  assert(acurite::form_decode("a%2Bb+c%ZZ") == "a+b c%ZZ");

  float parsed = 123;
  assert(acurite::finite_float("29.92", parsed));
  assert(std::fabs(parsed - 29.92f) < 0.0001f);
  for (const std::string value : {"", "nan", "NaN", "inf", "-inf", "1e100", "90garbage", "90 "}) {
    parsed = 123;
    assert(!acurite::finite_float(value, parsed));
    assert(parsed == 123);
  }
  assert(!acurite::finite_float(std::string("90\0garbage", 10), parsed));
  assert(acurite::has_weather_value(query));
  assert(acurite::has_weather_value("winddirdeg=180"));
  assert(acurite::has_weather_value("dailyrainin=0.01"));
  assert(acurite::has_weather_value("rainin=0.02"));
  assert(acurite::has_weather_value("dewptf=61"));
  assert(acurite::has_weather_value("dewpt=62"));
  assert(!acurite::has_weather_value("indoortempf=75&baromin=nan"));
}
