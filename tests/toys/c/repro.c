/* Reproducer: parse_record() trusts the length byte; a record "REC1" with
 * length 9 but only 2 payload bytes fails the assertion guarding the copy. */
#include <stdio.h>

#include "record.h"

int main(void) {
  static const unsigned char input[] = {'R', 'E', 'C', '1', 9, 'a', 'b'};
  char name[256];
  printf("parse_record: %d\n", parse_record(input, sizeof input, name, sizeof name));
  return 0;
}
