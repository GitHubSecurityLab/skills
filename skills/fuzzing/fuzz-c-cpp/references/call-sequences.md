# C/C++ call sequences

A stateful API (create, configure, use, destroy) gets a bounded loop of
calls that the input picks. The calls use only handles the harness holds:

<!-- ci: c-call-sequence -->
```cpp
extern "C" int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size) {
  FuzzedDataProvider fdp(data, size);
  std::vector<doc_t *> docs;  // the live handles
  for (int step = 0; step < 16 && fdp.remaining_bytes() > 0; step++) {
    int op = fdp.ConsumeIntegralInRange<int>(0, 3);
    if (op == 0) {
      std::string text = fdp.ConsumeRandomLengthString(4096);
      if (doc_t *d = doc_parse(text.data(), text.size())) docs.push_back(d);
      continue;
    }
    if (docs.empty()) continue;
    size_t i = fdp.ConsumeIntegralInRange<size_t>(0, docs.size() - 1);
    if (op == 1) {
      doc_set_mode(docs[i], fdp.ConsumeIntegralInRange<int>(0, 2));  // modes deployments use
    } else if (op == 2) {
      for (size_t k = 0; k < doc_count(docs[i]); k++) doc_get(docs[i], k);  // read every result back
    } else {
      doc_free(docs[i]);
      docs.erase(docs.begin() + i);
    }
  }
  for (doc_t *d : docs) doc_free(d);
  return 0;
}
```

A freed handle leaves `docs`, the table of live handles, and every handle
still in it is freed at the end. So a use-after-free or leak report is
the library's.

If the harness is a FuzzTest, a `std::vector<std::variant<...>>`
parameter replaces the loop. It has one alternative per call, holding
that call's arguments; its domain is
`VectorOf(VariantOf(...)).WithMaxSize(16)`.
