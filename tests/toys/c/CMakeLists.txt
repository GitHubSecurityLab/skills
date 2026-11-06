cmake_minimum_required(VERSION 3.16)
project(record C)

# A common idiom that the fuzzing build must override: Release adds
# -O3 -DNDEBUG, which would remove the assertion in parse_record().
if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE Release)
endif()

option(RECORD_BUILD_FUZZERS "Build fuzz targets" OFF)
set(FUZZING_ENGINE "-fsanitize=fuzzer" CACHE STRING "Link flags of the fuzzing engine")

add_library(record src/record.c)
if(RECORD_BUILD_FUZZERS)
  add_subdirectory(fuzz)
endif()
