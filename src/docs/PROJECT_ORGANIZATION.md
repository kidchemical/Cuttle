# Project Organization - October 12, 2025

## Overview

The Cuttle project has been organized with all documentation and testing utilities properly structured within the `src/` directory.

## Directory Structure

```
/path/to/Cuttle/
├── electron/                      # Electron app (UI wrapper)
├── test_output/                   # Test output files
├── src/                          # Main source directory
│   ├── api/                      # API endpoints
│   │   └── web_chat_api.py      # Main API server (MODIFIED: debug logging)
│   ├── bots/                     # Bot implementations
│   ├── cache/                    # Cache storage
│   ├── core/                     # Core functionality
│   ├── data/                     # Data storage
│   ├── docs/                     # 📚 All documentation
│   │   ├── development/          # Development guides
│   │   ├── electron/             # Electron-specific docs
│   │   ├── fixes/                # ⭐ Fix documentation
│   │   │   ├── README.md         # Fixes directory overview
│   │   │   ├── QUERY_REPORT_FIXES_INDEX.md  # Complete index
│   │   │   ├── ALL_FIXES_SUMMARY.md         # Master summary
│   │   │   ├── QUERY_REPORT_LLM_GRAPH_MISMATCH_FIX.md
│   │   │   ├── QUERY_REPORT_AGGREGATOR_NODE_FIX.md
│   │   │   ├── PARALLEL_EXPERT_NODE_ID_DUPLICATION_FIX.md
│   │   │   ├── AGENT_POOL_HALLUCINATION_FIX.md
│   │   │   ├── COORDINATOR_PARALLELIZATION_ENHANCEMENT.md
│   │   │   ├── TEST_ENHANCEMENTS_FOR_QUERY_REPORT_FIXES.md
│   │   │   └── [Individual fix summaries]
│   │   ├── guides/               # User guides
│   │   ├── setup/                # Setup instructions
│   │   └── testing/              # ⭐ Testing documentation
│   │       ├── README.md         # Testing directory overview
│   │       ├── RUN_THIS_TEST.md  # Step-by-step testing guide
│   │       └── TESTING_PIPELINE_FIXES.md  # Verification checklist
│   ├── managers/                 # System managers
│   ├── pipelines/                # Pipeline configurations
│   ├── rag/                      # RAG system
│   ├── reports/                  # Report generators
│   │   └── query_report_generator.py  # (MODIFIED: graph structure)
│   ├── requirements/             # Python dependencies
│   ├── scripts/                  # Scripts and utilities
│   │   ├── launchers/            # Launcher scripts
│   │   ├── setup/                # Setup scripts
│   │   └── utilities/            # ⭐ Utility scripts
│   │       ├── README.md         # Utilities directory overview
│   │       ├── automated_pipeline_test.py  # ⭐ Automated testing
│   │       ├── verify_specific_report.py   # Quick verification
│   │       └── run_test_on_report.py      # Focused test runner
│   ├── tests/                    # ⭐ Test suite
│   │   ├── test_query_report_consistency.py  # (ENHANCED: Tests 6 & 7)
│   │   └── [Other test files]
│   ├── tools/                    # Tool implementations
│   └── web/                      # Web UI
│       ├── js/                   # JavaScript files
│       │   └── pipeline_executor.js  # (MODIFIED: All fixes)
│       └── logs/                 # Query reports and logs
├── bot_config.json
├── build_electron.bat
├── start_electron.bat
└── LICENSE.txt
```

## Recent Changes (October 12, 2025)

### Files Moved from Root to Organized Locations

#### Documentation → `src/docs/fixes/`
- ✅ `AGENT_POOL_HALLUCINATION_FIX_SUMMARY.md`
- ✅ `ALL_FIXES_SUMMARY.md`
- ✅ `COORDINATOR_PARALLELIZATION_SUMMARY.md`
- ✅ `PARALLEL_EXPERT_FIX_SUMMARY.md`
- ✅ `QUERY_REPORT_SECOND_FIX_SUMMARY.md`
- ✅ `TEST_ENHANCEMENTS_SUMMARY.md`

#### Testing Guides → `src/docs/testing/`
- ✅ `RUN_THIS_TEST.md`
- ✅ `TESTING_PIPELINE_FIXES.md`

#### Test Scripts → `src/scripts/utilities/`
- ✅ `automated_pipeline_test.py` (path-aware)
- ✅ `verify_specific_report.py` (path-aware)
- ✅ `run_test_on_report.py` (path-aware)

### Path Updates

All moved scripts now use relative path resolution:

```python
# Scripts automatically find project root
script_dir = Path(__file__).parent
project_root = script_dir.parent.parent.parent
pipeline_path = project_root / 'src' / 'pipelines' / 'TEST_-_Hello_World_Parallelization.json'
```

This ensures scripts work regardless of where they're called from.

## Navigation

### For Users
- **Start here:** `src/docs/README.md`
- **Testing:** `src/docs/testing/README.md`
- **Fixes:** `src/docs/fixes/README.md`

### For Developers
- **Tests:** `src/tests/`
- **Scripts:** `src/scripts/utilities/`
- **API:** `src/api/web_chat_api.py`
- **Frontend:** `src/web/js/pipeline_executor.js`

## Quick Commands

### Run Automated Test
```bash
python src/scripts/utilities/automated_pipeline_test.py
```

### Verify Specific Report
```bash
python src/scripts/utilities/verify_specific_report.py src/web/logs/query_report_XXXXX.html
```

### Run Consistency Tests
```bash
cd src/tests
python test_query_report_consistency.py
```

## Benefits of Organization

1. ✅ **Clean root directory** - Only essential files
2. ✅ **Logical grouping** - Docs, tests, scripts in appropriate locations
3. ✅ **Easy navigation** - Clear directory structure
4. ✅ **Path-aware scripts** - All scripts work from any location
5. ✅ **Better maintenance** - Related files grouped together

## Verification Status

**Last Test:** October 12, 2025 at 14:11  
**Report:** `query_report_af486138_20251012_141139.html`  
**Result:** ✅ All 5 node IDs unique, all sections consistent  

**Pipeline executed with:**
- ✅ 3 experts in parallel
- ✅ Unique node IDs: #2, #3:1, #3:2, #3:3, #4
- ✅ All nodes tracked properly
- ✅ No duplicate IDs
- ✅ Aggregator executed (not skipped)

---

**Project Organization: COMPLETE** ✅

