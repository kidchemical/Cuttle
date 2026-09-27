# Testing Documentation

## Overview

This directory contains testing guides and documentation for verifying system functionality.

## Query Report Testing

### Getting Started

**📄 `RUN_THIS_TEST.md`** - Complete step-by-step guide for testing query reports  
**📄 `TESTING_PIPELINE_FIXES.md`** - Detailed verification checklist

### Quick Test

```bash
# Automated testing (recommended)
python src/scripts/utilities/automated_pipeline_test.py

# Manual consistency check
cd src/tests
python test_query_report_consistency.py
```

## Test Scripts

Located in: `../../scripts/utilities/`

- `automated_pipeline_test.py` - Execute pipeline and verify results automatically
- `verify_specific_report.py` - Quick verification of a specific report
- `run_test_on_report.py` - Run focused tests on a specific report

## Test Suite

Located in: `../../tests/`

- `test_query_report_consistency.py` - 7 comprehensive consistency tests
- `test_query_report_graph_error_handling.py` - Error handling tests

## Expected Test Results

After all fixes:

```
Test 1: [PASS] LLM call count matches LLM nodes in graph
Test 2: [PASS] All LLM calls reference valid LLM nodes
Test 3: [PASS] All LLM calls are sequential
Test 4: [PASS] Graph shows complete flow
Test 5: [PASS] All nodes shown in graph
Test 6: [PASS] All LLM calls use unique node IDs
Test 7: [PASS] All LLM nodes properly tracked

[SUCCESS] ALL TESTS PASSED ✅
```

## Integration Testing

**📄 `INTEGRATION_TEST_GUIDE.md`** - Full integration testing procedures

## Related Documentation

- **Fixes:** `../fixes/` - All fix documentation
- **Development:** `../development/` - Development guidelines
- **Guides:** `../guides/` - User guides

---

**Last updated:** October 12, 2025

