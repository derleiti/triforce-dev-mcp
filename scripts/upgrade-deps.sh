#!/usr/bin/env bash

set -e

echo "🚀 AILinux Backend - Dependency Upgrade Script"
echo "=============================================="
echo ""

# Check if virtual environment is activated
if [ -z "$VIRTUAL_ENV" ]; then
    echo "⚠️  Virtual environment not activated!"
    echo "Please run: source .venv/bin/activate"
    exit 1
fi

echo "📦 Current Python version:"
python --version
echo ""

# Backup current requirements
echo "💾 Backing up current requirements..."
cp requirements.txt requirements.txt.backup
echo "✅ Backup saved to requirements.txt.backup"
echo ""

# Show outdated packages
echo "📊 Checking for outdated packages..."
pip list --outdated
echo ""

# Upgrade all packages
read -p "🔄 Upgrade all packages to latest versions? (y/N) " -n 1 -r
echo ""
if [[ $REPLY =~ ^[Yy]$ ]]; then
    echo "⬆️  Upgrading packages..."
    pip install --upgrade -r requirements.txt
    echo "✅ Packages upgraded!"
    echo ""
fi

# Run tests
read -p "🧪 Run tests to verify compatibility? (y/N) " -n 1 -r
echo ""
if [[ $REPLY =~ ^[Yy]$ ]]; then
    echo "🧪 Running test suite..."
    if python -m pytest tests/ -v --tb=short 2>/dev/null; then
        echo "✅ All tests passed!"
    else
        echo "❌ Tests failed! Consider rolling back:"
        echo "   cp requirements.txt.backup requirements.txt"
        echo "   pip install -r requirements.txt"
        exit 1
    fi
    echo ""
fi

# Code quality checks
read -p "🔍 Run code quality checks? (y/N) " -n 1 -r
echo ""
if [[ $REPLY =~ ^[Yy]$ ]]; then
    echo "🔍 Running Ruff linter..."
    python -m ruff check app/ || true
    
    echo ""
    echo "🎨 Running Black formatter check..."
    python -m black --check app/ || true
    
    echo ""
    echo "📝 Running MyPy type checker..."
    python -m mypy app/ --ignore-missing-imports || true
fi

echo ""
echo "✨ Upgrade complete!"
echo ""
echo "📋 Next steps:"
echo "1. Review changes: git diff requirements.txt"
echo "2. Test locally: uvicorn app.main:app --reload"
echo "3. Commit changes: git add requirements.txt && git commit -m 'build: upgrade dependencies'"
echo "4. Deploy to production"
echo ""
echo "💡 Tip: Keep requirements.txt.backup for rollback if needed"
