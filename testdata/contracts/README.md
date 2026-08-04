# Golden contract fixtures

One JSON document per message on the gateway↔service wire. Both implementations
parse every file here with unknown-field rejection turned on:

- Go — `gateway/internal/contract/golden_test.go`
- Python — `python/tests/test_contracts.py`

That is how the two halves of the contract (ADR-0001) are kept honest without a
code generator. A field added on one side and forgotten on the other fails CI
here rather than at 3 a.m. in a demo.

**Changing a contract:** edit the fixture and both type definitions in the same
commit. If a test fails, the fixture is the specification — fix the code.
