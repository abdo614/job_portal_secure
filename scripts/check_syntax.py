import py_compile
import os
import sys
errors = []
for root, dirs, files in os.walk('.'):
    # تجاهل مجلدات افتراضية
    if any(part.startswith('.git') or part.startswith('__pycache__') for part in root.split(os.sep)):
        continue
    for f in files:
        if f.endswith('.py'):
            path = os.path.join(root, f)
            try:
                py_compile.compile(path, doraise=True)
            except Exception as e:
                errors.append((path, str(e)))

if errors:
    print('SYNTAX_ERRORS_FOUND')
    for p, e in errors:
        print(p)
        print(e)
    sys.exit(2)
print('ALL_PYTHON_FILES_COMPILED_OK')
