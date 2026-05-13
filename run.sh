#!/bin/bash
# Wrapper script to run the app with OpenMP fix

export KMP_DUPLICATE_LIB_OK=TRUE
source .venv/bin/activate
python -m app.main --terminal-stream

python -m app.main --terminal




Next steps:
1. Set environment variable:
   export KMP_DUPLICATE_LIB_OK=TRUE

2. Run streaming mode:
   python -m app.main --terminal-stream

3. Or run blocking mode:
   python -m app.main --terminal