add_executable(record_fuzzer fuzz_record.c)
target_link_libraries(record_fuzzer PRIVATE record ${FUZZING_ENGINE})
