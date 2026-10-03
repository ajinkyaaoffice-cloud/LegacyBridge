# Reference translations (offline mode only)

Hand-written Python translations of `../src/*.cbl`, verified against GnuCOBOL golden outputs.
They are used ONLY by the MockProvider (`--dry-run`, tests, `eval --dry-run`) so the whole
pipeline can be exercised without network access. Real LLM runs never read this directory.
