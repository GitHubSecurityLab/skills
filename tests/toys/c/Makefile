# An in-tree build, for fuzz-c-cpp's build.md (In-tree builds): CC, CFLAGS and LDFLAGS come from the command line.
fuzz_record: fuzz/fuzz_record.c librecord.a
	$(CC) $(CFLAGS) fuzz/fuzz_record.c librecord.a $(LDFLAGS) -o $@

librecord.a: src/record.o
	$(AR) rcs $@ src/record.o
