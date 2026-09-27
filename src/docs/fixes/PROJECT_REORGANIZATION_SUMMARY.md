# Project Directory Structure Reorganization

## Overview
This document summarizes the reorganization of the Cuttle project directory structure to ensure all source files are properly organized within the `src/` directory.

## Changes Made

### Test Files Moved to `src/tests/`
The following test files were moved from the root directory to `src/tests/`:

1. **test_query_report_consistency.py** → `src/tests/test_query_report_consistency.py`
   - Updated paths: `web/data/...` → uses `Path(__file__).parent.parent.parent / "web" / "data"`
   - Updated paths: `web/logs/...` → uses `Path(__file__).parent.parent.parent / "web" / "logs"`

2. **test_agent_pool_graph_consistency.py** → `src/tests/test_agent_pool_graph_consistency.py`
   - No path updates needed (uses in-memory test data)

3. **check_parallel_report.py** → `src/tests/check_parallel_report.py`
   - Updated paths: `web/logs/...` → uses `Path(__file__).parent.parent.parent / "web" / "logs"`

4. **verify_all_reports.py** → `src/tests/verify_all_reports.py`
   - Updated paths: `web/logs` → uses `Path(__file__).parent.parent.parent / "web" / "logs"`

5. **test_hello_world_parallelization.py** → `src/tests/test_hello_world_parallelization.py`
   - Updated subprocess call to use proper path resolution

6. **test_user_parallelization_scenario.py** → `src/tests/test_user_parallelization_scenario.py`
   - No path updates needed (analysis script only)

### Utility Scripts Moved to `src/scripts/`
The following utility scripts were moved from the root directory to `src/scripts/`:

1. **create_parallel_test.py** → `src/scripts/create_parallel_test.py`
   - No path updates needed (uses API calls only)

2. **create_test_execution.py** → `src/scripts/create_test_execution.py`
   - No path updates needed (uses API calls only)

3. **create_mock_parallelization_report.py** → `src/scripts/create_mock_parallelization_report.py`
   - Updated paths: `web/logs` → uses `Path(__file__).parent.parent.parent / "web" / "logs"`

## Directory Structure After Reorganization

```
/path/to/Cuttle/
├── bot_config.json
├── build_electron.bat
├── start_electron.bat
├── projects.db
├── Cuttle.code-workspace
├── LICENSE.txt
├── [Documentation files: *.md]
├── electron\
│   └── [Electron app files]
├── web\
│   └── logs\
│       └── [Query report files]
└── src\
    ├── [Main application source files]
    ├── tests\
    │   ├── test_query_report_consistency.py ✨ MOVED
    │   ├── test_agent_pool_graph_consistency.py ✨ MOVED
    │   ├── check_parallel_report.py ✨ MOVED
    │   ├── verify_all_reports.py ✨ MOVED
    │   ├── test_hello_world_parallelization.py ✨ MOVED
    │   ├── test_user_parallelization_scenario.py ✨ MOVED
    │   ├── integration\
    │   └── unit\
    └── scripts\
        ├── create_parallel_test.py ✨ MOVED
        ├── create_test_execution.py ✨ MOVED
        ├── create_mock_parallelization_report.py ✨ MOVED
        └── [Other utility scripts]
```

## Benefits

1. **Cleaner Root Directory**: The root directory now only contains configuration files, batch scripts, and documentation.

2. **Organized Structure**: All source code, tests, and scripts are properly organized within the `src/` directory.

3. **Consistent Paths**: All moved files use proper path resolution with `Path(__file__).parent` to work correctly from their new locations.

4. **Better Maintainability**: Developers can now easily find tests in `src/tests/` and utility scripts in `src/scripts/`.

## Running Tests from New Location

To run tests from their new location:

```bash
# From project root:
python src/tests/test_query_report_consistency.py

# Or from within src/tests/:
cd src/tests
python test_query_report_consistency.py
```

## Running Utility Scripts

To run utility scripts from their new location:

```bash
# From project root:
python src/scripts/create_test_execution.py

# Or from within src/scripts/:
cd src/scripts
python create_test_execution.py
```

## Notes

- All path references have been updated to use `Path(__file__).parent` for relative path resolution
- No functionality was changed, only file locations and path references
- Original files in the root directory have been removed after successful migration

