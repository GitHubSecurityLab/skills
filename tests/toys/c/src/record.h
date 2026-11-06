#ifndef RECORD_H
#define RECORD_H
#include <stddef.h>
int parse_record(const unsigned char *data, size_t size, char *name, size_t name_size);
#endif
