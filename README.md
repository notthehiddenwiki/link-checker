# Link Checker for Markdown Files

A fast and reliable tool that scans markdown files for HTTP/HTTPS links and checks their availability. Perfect for maintaining documentation, blogs, and repositories with external references.

## Features

- **Multi-format support**: Detects links in markdown syntax, HTML tags, and bare URLs
- **Smart link handling**: Properly handles complex URLs with parentheses and special characters
- **Concurrent checking**: Fast parallel processing with rate limiting
- **Multiple report formats**: JSON, CSV, and HTML reports
- **Domain-specific optimization**: Custom handling for popular platforms (GitHub, YouTube, LinkedIn, etc.)
- **Error resilience**: Automatic retries with exponential backoff
- **Browser-based checking**: Uses Playwright for sites that block automated requests

## Quick Start

### Installation

1. Install Python dependencies:
```bash
pip install -r requirements.txt
```

2. Install Playwright browser:
```bash
playwright install chromium
```

### Basic Usage

Check all markdown files in a directory:
```bash
python link_checker.py ./docs
```

Enable debug mode for detailed output:
```bash
python link_checker.py ./docs --debug
```

## How It Works

1. **Discovery**: Recursively finds all `.md` files in the specified directory
2. **Extraction**: Uses regex patterns to find links in various formats:
   - Markdown links: `[text](url)`
   - Image links: `![alt](url)`
   - HTML attributes: `href="url"` and `src="url"`
   - Bare URLs: `https://example.com`
   - Angle bracket URLs: `<https://example.com>`
3. **Classification**: Categorizes links (web, PDF, image, document)
4. **Checking**: Uses appropriate method (HTTP requests or browser automation)
5. **Reporting**: Generates comprehensive reports in multiple formats

## Output

### Console Output
Real-time progress with summary statistics:
```
Found 150 markdown files
Found 500 total link occurrences
Checking 300 unique links...
[1/300] Checking: https://example.com
...
Completed in 45.2 seconds

LINK CHECK SUMMARY
Total links checked: 300
Successful: 285 (95.0%)
Failed: 15 (5.0%)
```

### Generated Reports

Reports are saved in the `reports/` directory with timestamps:

- **JSON Report**: Complete results with metadata (`link_check_report_YYYYMMDD_HHMMSS.json`)
- **CSV Report**: Broken links only for easy analysis (`broken_links_YYYYMMDD_HHMMSS.csv`)
- **HTML Report**: Interactive web report with filtering (`link_check_report_YYYYMMDD_HHMMSS.html`)

## Configuration

The tool includes built-in optimizations for popular domains:
- **Social platforms**: LinkedIn, Twitter/X, Instagram, Facebook
- **Development**: GitHub, Stack Overflow
- **Learning**: YouTube, Coursera, Udemy
- **Security**: HackTheBox, various security blogs

Rate limiting and retry strategies are automatically adjusted per domain.

## Performance

- Processes ~300 links in under 60 seconds (depends on network and target sites)
- Concurrent limit: 10 simultaneous checks (configurable)
- Automatic rate limiting prevents overwhelming target servers
- Smart retries handle temporary failures

## Common Use Cases

- **Documentation maintenance**: Ensure all external references are valid
- **Blog post verification**: Check links before publishing
- **Repository cleanup**: Find and fix broken links in README files
- **Content auditing**: Regular checks of large documentation sites

## Troubleshooting

**Timeout errors**: Some sites are slow to respond. The tool automatically retries with longer timeouts.

**Rate limiting (429 errors)**: The tool respects rate limits and waits before retrying.

**Bot detection**: Uses browser automation for sites that block automated requests.

**Install issues**: Make sure you have Python 3.8+ and run `playwright install chromium` after pip install.

## Requirements

- Python 3.8+
- Dependencies listed in `requirements.txt`
- Chromium browser (installed via Playwright)

## License

This tool is designed for responsible link checking. Please respect website terms of service and rate limits.
# link-checker
