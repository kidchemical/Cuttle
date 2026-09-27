# Output Directory

This directory contains generated files and temporary data from the Cuttle.

## Structure

```
output/
├── README.md          # This file
├── temp/              # Temporary files (auto-generated)
│   ├── *.png          # Screenshots
│   ├── *.txt          # Temporary text files
│   ├── *.json         # Temporary JSON files
│   └── ...            # Other temporary files
└── [future folders]   # Additional output categories as needed
```

## File Types

### `temp/` Directory
- **Screenshots**: `screenshot_*.png`, `*_window_*.png`, `region_*.png`
- **OCR Text Files**: Temporary text extraction files
- **Cursor AI Requests**: `cursor_ai_request_*.json`
- **Temporary Prompts**: `temp_cursor_prompt_*.txt`
- **Other Temporary Files**: Generated during bot operations

## Cleanup

Files in the `temp/` directory are automatically generated and can be safely deleted. The bot will create new files as needed.

## Git Ignore

The entire `output/` directory is ignored by Git to prevent committing temporary and generated files.

## Usage

The bot automatically saves files here when:
- Taking screenshots
- Extracting text from images
- Creating temporary files for processing
- Storing intermediate results

No manual intervention is required - the bot manages this directory automatically.
