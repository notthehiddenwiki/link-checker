#!/usr/bin/env python3
"""
Link Checker for Markdown Files
Checks all links in markdown files within a directory and generates reports
"""

import asyncio
import re
import sys
import json
import time
import logging
import traceback
from datetime import datetime
from pathlib import Path
from typing import List, Dict
from urllib.parse import urlparse
from dataclasses import dataclass, asdict

try:
    from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError
    import aiofiles
    import aiohttp
except ImportError:
    print("Missing dependencies. Install with:")
    print("pip install playwright aiofiles aiohttp")
    print("playwright install chromium")
    sys.exit(1)

@dataclass
class LinkResult:
    url: str
    status_code: int
    final_url: str
    message: str
    is_pdf: bool
    is_image: bool
    source_file: str
    timestamp: str

class SimpleLinkChecker:
    def __init__(self, debug=False):
        self.timeout = 30000
        self.delay = 1000
        self.concurrent_limit = 10
        self.user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        self.results = []
        self.all_links = []
        self.link_sources = {}
        self.rate_limit_tracker = {}
        self.max_retries = 5
        self.debug = debug
        
        if debug:
            logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')
        else:
            logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
        
        self.logger = logging.getLogger(__name__)
        
        # Special handling for known platforms
        self.special_domains = {
            "linkedin.com": {"wait_for": "networkidle", "extra_delay": 6000, "rate_limit_delay": 4000, "max_retries": 5},
            "medium.com": {"wait_for": "networkidle", "extra_delay": 4000, "rate_limit_delay": 3000, "max_retries": 5},
            "youtube.com": {"wait_for": "domcontentloaded", "extra_delay": 6000, "retry_timeout": True, "timeout_multiplier": 3, "rate_limit_delay": 4000, "max_retries": 5},
            "youtu.be": {"wait_for": "domcontentloaded", "extra_delay": 6000, "retry_timeout": True, "timeout_multiplier": 3, "rate_limit_delay": 4000, "max_retries": 5},
            "coursera.org": {"wait_for": "networkidle", "extra_delay": 7000, "rate_limit_delay": 5000, "max_retries": 5},
            "udemy.com": {"wait_for": "networkidle", "extra_delay": 5000, "rate_limit_delay": 3000, "max_retries": 5},
            "stackoverflow.com": {"wait_for": "domcontentloaded", "extra_delay": 4000, "retry_403": True, "timeout_multiplier": 2, "rate_limit_delay": 2000, "max_retries": 5},
            "github.com": {"wait_for": "domcontentloaded", "extra_delay": 2000, "rate_limit_delay": 1000, "max_retries": 5},
            "raw.githubusercontent.com": {"wait_for": "load", "extra_delay": 1000, "rate_limit_delay": 500, "max_retries": 3, "use_http_check": True},
            "reddit.com": {"wait_for": "networkidle", "extra_delay": 3000, "rate_limit_delay": 2000, "max_retries": 5},
            "twitter.com": {"wait_for": "networkidle", "extra_delay": 4000, "rate_limit_delay": 3000, "max_retries": 5},
            "x.com": {"wait_for": "networkidle", "extra_delay": 4000, "rate_limit_delay": 3000, "max_retries": 5},
            "instagram.com": {"wait_for": "networkidle", "extra_delay": 4000, "rate_limit_delay": 3000, "max_retries": 5},
            "facebook.com": {"wait_for": "networkidle", "extra_delay": 4000, "rate_limit_delay": 3000, "max_retries": 5},
            "cloudflare.com": {"wait_for": "networkidle", "extra_delay": 5000, "rate_limit_delay": 3000, "max_retries": 5},
            "discord.com": {"wait_for": "networkidle", "extra_delay": 3000, "rate_limit_delay": 2000, "max_retries": 5},
            "hackthebox.com": {"wait_for": "networkidle", "extra_delay": 8000, "rate_limit_delay": 6000, "max_retries": 7, "timeout_multiplier": 2},
            "academy.hackthebox.com": {"wait_for": "networkidle", "extra_delay": 10000, "rate_limit_delay": 8000, "max_retries": 8, "timeout_multiplier": 3},
            "researchgate.net": {"wait_for": "networkidle", "extra_delay": 5000, "rate_limit_delay": 4000, "max_retries": 5},
            "iso.org": {"wait_for": "networkidle", "extra_delay": 6000, "rate_limit_delay": 4000, "max_retries": 5},
            "kapitanhack.pl": {"wait_for": "networkidle", "extra_delay": 5000, "rate_limit_delay": 3000, "max_retries": 5, "timeout_multiplier": 2},
            "hackers-arise.com": {"wait_for": "networkidle", "extra_delay": 6000, "rate_limit_delay": 4000, "max_retries": 5, "timeout_multiplier": 2}
        }

    def extract_links_from_markdown(self, content: str) -> List[str]:
        """Extract all HTTP(S) links from markdown content"""
        
        def extract_markdown_link_url(text, start_pos):
            """Extract URL from markdown link format [text](url) with proper parentheses balancing"""
            paren_count = 1
            url_start = start_pos
            i = start_pos
            
            while i < len(text):
                char = text[i]
                if char == '(':
                    paren_count += 1
                elif char == ')':
                    paren_count -= 1
                    if paren_count == 0:
                        return text[url_start:i]
                elif char in [' ', '\n', '\t'] and paren_count == 1:
                    break
                i += 1
            
            return text[url_start:i]
        
        links = []
        
        # Markdown links [text](url)
        markdown_pattern = r'\[([^\]]*)\]\('
        for match in re.finditer(markdown_pattern, content):
            url_start = match.end()
            url = extract_markdown_link_url(content, url_start)
            if url and url.startswith(('http://', 'https://')):
                links.append(url)
        
        # Image links ![alt](url)
        image_pattern = r'!\[([^\]]*)\]\('
        for match in re.finditer(image_pattern, content):
            url_start = match.end()
            url = extract_markdown_link_url(content, url_start)
            if url and url.startswith(('http://', 'https://')):
                links.append(url)
        
        # Bare URLs
        bare_url_pattern = r'(?<!\]\()(https?://[^\s<>"\'`\]]+)'
        for match in re.finditer(bare_url_pattern, content):
            start_pos = match.start()
            preceding_text = content[max(0, start_pos-100):start_pos]
            if re.search(r'\[[^\]]*\]\($', preceding_text):
                continue
                
            url = match.group(1)
            url = re.sub(r'[.,;!?]+$', '', url)
            open_parens = url.count('(')
            close_parens = url.count(')')
            if close_parens > open_parens:
                while url.endswith(')') and url.count(')') > url.count('('):
                    url = url[:-1]
            if url:
                links.append(url)
        
        # HTML href attributes
        href_pattern = r'href=["\']([^"\']+)["\']'
        for match in re.finditer(href_pattern, content, re.IGNORECASE):
            url = match.group(1)
            if url.startswith(('http://', 'https://')):
                links.append(url)
        
        # HTML src attributes
        src_pattern = r'src=["\']([^"\']+)["\']'
        for match in re.finditer(src_pattern, content, re.IGNORECASE):
            url = match.group(1)
            if url.startswith(('http://', 'https://')):
                links.append(url)
        
        # Angle bracket URLs <url>
        angle_pattern = r'<(https?://[^>]+)>'
        for match in re.finditer(angle_pattern, content):
            url = match.group(1)
            links.append(url)
        
        # Clean and validate links
        cleaned_links = []
        for link in links:
            if link.startswith(('http://', 'https://')):
                link = re.sub(r'["\']$', '', link).strip()
                
                if '#' in link and not any(x in link for x in ['github.com', 'stackoverflow.com', 'docs.', 'wiki']):
                    if not re.search(r'#[a-zA-Z0-9_-]+$', link):
                        link = link.split('#')[0]
                
                if (link and 
                    len(link) > 10 and
                    '.' in link and
                    not link.endswith('//') and
                    not any(invalid in link.lower() for invalid in ['localhost', '127.0.0.1', 'example.com', 'test.com', 'your-domain.com'])):
                    cleaned_links.append(link)
        
        return cleaned_links

    async def check_http_link(self, url: str, max_retries: int) -> LinkResult:
        """Check links using HTTP requests"""
        retries = 0
        while retries < max_retries:
            try:
                headers = {
                    'User-Agent': self.user_agent,
                    'Accept': '*/*',
                    'Accept-Language': 'en-US,en;q=0.9',
                    'Cache-Control': 'no-cache'
                }
                
                timeout = aiohttp.ClientTimeout(total=(self.timeout / 1000) + (retries * 5))
                
                async with aiohttp.ClientSession(
                    headers=headers, 
                    timeout=timeout,
                    connector=aiohttp.TCPConnector(limit=100, ttl_dns_cache=300)
                ) as session:
                    
                    try:
                        async with session.head(url, allow_redirects=True) as response:
                            return LinkResult(
                                url=url,
                                status_code=response.status,
                                final_url=str(response.url),
                                message="OK" if response.status < 400 else f"Error {response.status}",
                                is_pdf=url.lower().endswith('.pdf'),
                                is_image=any(url.lower().endswith(ext) for ext in ['.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg']),
                                source_file="",
                                timestamp=datetime.now().isoformat()
                            )
                    except aiohttp.ClientResponseError as e:
                        if e.status == 405:  # Method not allowed, try GET
                            async with session.get(url, allow_redirects=True) as response:
                                return LinkResult(
                                    url=url,
                                    status_code=response.status,
                                    final_url=str(response.url),
                                    message="OK" if response.status < 400 else f"Error {response.status}",
                                    is_pdf=url.lower().endswith('.pdf'),
                                    is_image=any(url.lower().endswith(ext) for ext in ['.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg']),
                                    source_file="",
                                    timestamp=datetime.now().isoformat()
                                )
                        else:
                            return LinkResult(
                                url=url,
                                status_code=e.status,
                                final_url=url,
                                message=f"HTTP Error {e.status}",
                                is_pdf=False,
                                is_image=False,
                                source_file="",
                                timestamp=datetime.now().isoformat()
                            )
                            
            except Exception as e:
                retries += 1
                if retries >= max_retries:
                    return LinkResult(
                        url=url,
                        status_code=500,
                        final_url=url,
                        message=f"Error after {retries} retries: {str(e)[:50]}",
                        is_pdf=False,
                        is_image=False,
                        source_file="",
                        timestamp=datetime.now().isoformat()
                    )
                
                await asyncio.sleep(1 * (2 ** retries))
                
        return LinkResult(
            url=url,
            status_code=500,
            final_url=url,
            message="Max retries exceeded",
            is_pdf=False,
            is_image=False,
            source_file="",
            timestamp=datetime.now().isoformat()
        )

    async def check_web_link(self, browser, url: str, max_retries: int) -> LinkResult:
        """Check web links using browser with enhanced bot detection evasion"""
        retries = 0
        while retries < max_retries:
            context = None
            try:
                # Enhanced context for better bot detection bypass
                context = await browser.new_context(
                    user_agent=self.user_agent,
                    viewport={"width": 1920, "height": 1080},
                    locale='en-US',
                    timezone_id='America/New_York',
                    geolocation={'latitude': 40.7128, 'longitude': -74.0060},
                    permissions=['geolocation'],
                    extra_http_headers={
                        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
                        "Accept-Language": "en-US,en;q=0.9",
                        "Accept-Encoding": "gzip, deflate, br",
                        "DNT": "1",
                        "Connection": "keep-alive",
                        "Upgrade-Insecure-Requests": "1",
                        "Sec-Fetch-Dest": "document",
                        "Sec-Fetch-Mode": "navigate",
                        "Sec-Fetch-Site": "none",
                        "Sec-Fetch-User": "?1",
                        "Cache-Control": "max-age=0",
                        "sec-ch-ua": '"Google Chrome";v="122", "Chromium";v="122", "Not(A:Brand";v="24"',
                        "sec-ch-ua-mobile": "?0",
                        "sec-ch-ua-platform": '"Windows"'
                    }
                )
                page = await context.new_page()
                
                # Enhanced script to remove webdriver detection
                await page.add_init_script("""
                    Object.defineProperty(navigator, 'webdriver', {
                        get: () => undefined,
                    });
                    
                    Object.defineProperty(navigator, 'languages', {
                        get: () => ['en-US', 'en'],
                    });
                    
                    Object.defineProperty(navigator, 'plugins', {
                        get: () => ({
                            length: 5,
                            0: {name: 'Chrome PDF Plugin'},
                            1: {name: 'Chrome PDF Viewer'},
                            2: {name: 'Native Client'},
                            3: {name: 'WebKit built-in PDF'},
                            4: {name: 'PDF Viewer'}
                        }),
                    });
                    
                    Object.defineProperty(screen, 'colorDepth', { get: () => 24 });
                    Object.defineProperty(screen, 'pixelDepth', { get: () => 24 });
                """)
                
                # Get domain settings
                domain = urlparse(url).netloc.lower()
                wait_for = "load"
                extra_delay = 0
                retry_403 = False
                timeout_multiplier = 1
                
                for special_domain, settings in self.special_domains.items():
                    if special_domain in domain:
                        wait_for = settings.get("wait_for", "load")
                        extra_delay = settings.get("extra_delay", 0)
                        retry_403 = settings.get("retry_403", False)
                        timeout_multiplier = settings.get("timeout_multiplier", 1)
                        break
                
                # Add human-like delay
                await asyncio.sleep(0.3 + (hash(url) % 1000) / 3000)
                
                adjusted_timeout = (self.timeout + (retries * 10000)) * timeout_multiplier
                
                response = await page.goto(url, wait_until=wait_for, timeout=adjusted_timeout)
                
                if not response:
                    await context.close()
                    return LinkResult(
                        url=url,
                        status_code=0,
                        final_url=url,
                        message="No response",
                        is_pdf=False,
                        is_image=False,
                        source_file="",
                        timestamp=datetime.now().isoformat()
                    )
                
                if extra_delay > 0:
                    await asyncio.sleep(extra_delay / 1000)
                
                status_code = response.status
                final_url = page.url
                
                # Enhanced error handling
                if status_code == 429:  # Rate limited
                    await context.close()
                    if retries < max_retries - 1:
                        retries += 1
                        await asyncio.sleep(10 * retries)
                        continue
                    else:
                        message = f"Rate limited after {max_retries} attempts"
                        
                elif status_code == 403 and retry_403:
                    try:
                        title = await page.title()
                        content = await page.content()
                        
                        if (title and len(title) > 10 and 
                            "access denied" not in title.lower() and 
                            "forbidden" not in title.lower() and
                            len(content) > 5000):
                            status_code = 200
                            message = "OK (403 bypassed - content accessible)"
                        else:
                            message = "Blocked (403 Forbidden)"
                    except:
                        message = "Blocked (403 Forbidden)"
                        
                elif status_code == 999:  # LinkedIn bot blocking
                    try:
                        title = await page.title()
                        if title and len(title) > 0 and "block" not in title.lower():
                            message = "OK (bot detection bypassed)"
                            status_code = 200
                        else:
                            message = "Blocked (bot detection)"
                    except:
                        message = "Blocked (bot detection)"
                        
                elif status_code < 400:
                    message = "OK"
                else:
                    message = f"Error {status_code}"
                
                await context.close()
                return LinkResult(
                    url=url,
                    status_code=status_code,
                    final_url=final_url,
                    message=message,
                    is_pdf=False,
                    is_image=False,
                    source_file="",
                    timestamp=datetime.now().isoformat()
                )
                
            except PlaywrightTimeoutError:
                if context:
                    await context.close()
                    
                if retries < max_retries - 1:
                    retries += 1
                    await asyncio.sleep(5 * retries)
                    continue
                    
                return LinkResult(
                    url=url,
                    status_code=408,
                    final_url=url,
                    message=f"Timeout after {retries + 1} attempts",
                    is_pdf=False,
                    is_image=False,
                    source_file="",
                    timestamp=datetime.now().isoformat()
                )
                
            except Exception as e:
                if context:
                    await context.close()
                    
                if retries < max_retries - 1:
                    retries += 1
                    await asyncio.sleep(2 * retries)
                    continue
                    
                return LinkResult(
                    url=url,
                    status_code=500,
                    final_url=url,
                    message=f"Error after {retries + 1} attempts: {str(e)[:50]}",
                    is_pdf=False,
                    is_image=False,
                    source_file="",
                    timestamp=datetime.now().isoformat()
                )
        
        return LinkResult(
            url=url,
            status_code=500,
            final_url=url,
            message="Max retries exceeded",
            is_pdf=False,
            is_image=False,
            source_file="",
            timestamp=datetime.now().isoformat()
        )

    def classify_link_type(self, url: str) -> str:
        """Classify link type for appropriate checking method"""
        url_lower = url.lower()
        
        if (url_lower.endswith('.pdf') or '/pdf/' in url_lower or 'filetype=pdf' in url_lower):
            return 'pdf'
        
        image_extensions = ('.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg', '.bmp', '.tiff', '.avif', '.heic', '.ico')
        if url_lower.endswith(image_extensions):
            return 'image'
        
        media_extensions = ('.mp4', '.avi', '.mov', '.wmv', '.flv', '.webm', '.mp3', '.wav', '.ogg')
        if url_lower.endswith(media_extensions):
            return 'media'
        
        doc_extensions = ('.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx', '.zip', '.rar', '.tar.gz')
        if url_lower.endswith(doc_extensions):
            return 'document'
        
        return 'web'

    async def apply_rate_limiting(self, url: str):
        """Apply domain-specific rate limiting"""
        domain = urlparse(url).netloc.lower()
        
        rate_limit_delay = 0
        for special_domain, settings in self.special_domains.items():
            if special_domain in domain:
                rate_limit_delay = settings.get("rate_limit_delay", 0)
                break
        
        if domain not in self.rate_limit_tracker:
            self.rate_limit_tracker[domain] = {'last_access': 0, 'consecutive_failures': 0, 'rate_limited_count': 0}
        
        tracker = self.rate_limit_tracker[domain]
        current_time = time.time()
        time_since_last = current_time - tracker['last_access']
        
        base_delay = max(self.delay / 1000, rate_limit_delay / 1000)
        
        # Increase delay if we've had rate limiting issues
        if tracker['rate_limited_count'] > 0:
            adaptive_multiplier = min(1 + (tracker['rate_limited_count'] * 0.5), 3.0)
            base_delay *= adaptive_multiplier
            
        # Additional delay for consecutive failures
        if tracker['consecutive_failures'] > 0:
            failure_multiplier = min(1 + (tracker['consecutive_failures'] * 0.3), 2.0)
            base_delay *= failure_multiplier
        
        if time_since_last < base_delay:
            sleep_time = base_delay - time_since_last
            if self.debug:
                self.logger.debug(f"Rate limiting {domain}: sleeping {sleep_time:.2f}s")
            await asyncio.sleep(sleep_time)
        
        tracker['last_access'] = time.time()

    def record_request_result(self, url: str, status_code: int):
        """Record the result of a request for adaptive rate limiting"""
        domain = urlparse(url).netloc.lower()
        if domain in self.rate_limit_tracker:
            tracker = self.rate_limit_tracker[domain]
            
            if status_code == 429:  # Rate limited
                tracker['rate_limited_count'] += 1
                tracker['consecutive_failures'] += 1
            elif status_code >= 500:  # Server errors
                tracker['consecutive_failures'] += 1
            elif status_code < 400:  # Success
                tracker['consecutive_failures'] = max(0, tracker['consecutive_failures'] - 1)

    async def check_link(self, browser, url: str) -> LinkResult:
        """Check a single link using the appropriate method"""
        await self.apply_rate_limiting(url)
        
        domain = urlparse(url).netloc.lower()
        max_retries = self.max_retries
        use_http_check = False
        
        for special_domain, settings in self.special_domains.items():
            if special_domain in domain:
                max_retries = settings.get("max_retries", self.max_retries)
                use_http_check = settings.get("use_http_check", False)
                break
        
        if use_http_check or "raw.githubusercontent.com" in domain:
            result = await self.check_http_link(url, max_retries)
        else:
            link_type = self.classify_link_type(url)
            if link_type in ['pdf', 'image', 'media', 'document']:
                result = await self.check_http_link(url, max_retries)
            else:
                result = await self.check_web_link(browser, url, max_retries)
        
        self.record_request_result(url, result.status_code)
        return result

    async def check_links_with_semaphore(self, semaphore: asyncio.Semaphore, browser, url: str, index: int, total: int) -> LinkResult:
        """Check link with concurrency control"""
        async with semaphore:
            print(f"[{index}/{total}] Checking: {url}")
            result = await self.check_link(browser, url)
            await asyncio.sleep(0.1)
            return result

    async def check_directory(self, directory: str):
        """Check all markdown files in directory"""
        dir_path = Path(directory)
        
        if not dir_path.exists() or not dir_path.is_dir():
            print(f"Error: {directory} is not a valid directory")
            return
        
        # Find all markdown files
        md_files = list(dir_path.rglob("*.md"))
        if not md_files:
            print("No markdown files found")
            return
        
        print(f"Found {len(md_files)} markdown files")
        
        # Extract all links from all files
        link_to_files = {}
        total_occurrences = 0
        
        for md_file in md_files:
            try:
                async with aiofiles.open(md_file, 'r', encoding='utf-8') as f:
                    content = await f.read()
                    links = self.extract_links_from_markdown(content)
                    
                    for link in links:
                        if link not in link_to_files:
                            link_to_files[link] = set()
                        link_to_files[link].add(str(md_file.relative_to(dir_path)))
                        total_occurrences += 1
                        
            except Exception as e:
                print(f"Error reading {md_file}: {e}")
        
        print(f"Found {total_occurrences} total link occurrences")
        
        # Create deduplicated list for checking
        self.all_links = list(link_to_files.keys())
        self.link_sources = {}
        self.total_occurrences = total_occurrences
        
        for link, files in link_to_files.items():
            sorted_files = sorted(files)
            if len(sorted_files) == 1:
                self.link_sources[link] = sorted_files[0]
            else:
                self.link_sources[link] = f"({len(sorted_files)} files) " + ", ".join(sorted_files)
        
        if not self.all_links:
            print("No links found")
            return
        
        print(f"Checking {len(self.all_links)} unique links...")
        
        # Store the file mapping for detailed reporting
        self.link_to_files = link_to_files
        
        # Check links
        start_time = time.time()
        
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            semaphore = asyncio.Semaphore(self.concurrent_limit)
            
            tasks = [
                self.check_links_with_semaphore(semaphore, browser, link, i+1, len(self.all_links)) 
                for i, link in enumerate(self.all_links)
            ]
            
            self.results = await asyncio.gather(*tasks, return_exceptions=True)
            
            # Handle exceptions and update source file info
            for i, result in enumerate(self.results):
                if isinstance(result, Exception):
                    self.results[i] = LinkResult(
                        url=self.all_links[i],
                        status_code=500,
                        final_url=self.all_links[i],
                        message=f"Exception: {str(result)[:50]}",
                        is_pdf=False,
                        is_image=False,
                        source_file=self.link_sources.get(self.all_links[i], ""),
                        timestamp=datetime.now().isoformat()
                    )
                else:
                    result.source_file = self.link_sources.get(result.url, "")
            
            await browser.close()
        
        duration = time.time() - start_time
        print(f"Completed in {duration:.1f} seconds")
        
        # Generate reports
        await self.generate_reports(directory)
        self.print_summary()

    async def generate_reports(self, directory: str):
        """Generate reports"""
        reports_dir = Path("reports")
        reports_dir.mkdir(exist_ok=True)
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        # Generate JSON report
        report_file = reports_dir / f"link_check_report_{timestamp}.json"
        failed_links = [r for r in self.results if r.status_code >= 400]
        successful_links = [r for r in self.results if r.status_code < 400]
        
        report_data = {
            "timestamp": datetime.now().isoformat(),
            "directory": directory,
            "summary": {
                "total_links": len(self.results),
                "successful": len(successful_links),
                "failed": len(failed_links),
                "pdfs": len([r for r in self.results if r.is_pdf]),
                "images": len([r for r in self.results if r.is_image])
            },
            "failed_links": [asdict(r) for r in failed_links],
            "all_results": [asdict(r) for r in self.results]
        }
        
        async with aiofiles.open(report_file, 'w', encoding='utf-8') as f:
            await f.write(json.dumps(report_data, indent=2))
        
        # Generate CSV report for broken links
        await self.generate_csv_report(reports_dir / f"broken_links_{timestamp}.csv")
        
        # Generate HTML report with filtering
        await self.generate_html_report(reports_dir / f"link_check_report_{timestamp}.html")
        
        print(f"Reports saved to: {reports_dir}")

    async def generate_csv_report(self, filepath: Path):
        """Generate CSV report for broken links"""
        try:
            import csv
            failed_links = [r for r in self.results if r.status_code >= 400]
            
            if failed_links:
                failed_links.sort(key=lambda x: (x.status_code, x.url.lower()))
                
                with open(filepath, 'w', newline='', encoding='utf-8') as csvfile:
                    fieldnames = ['Status Code', 'URL', 'Error Message', 'Source File', 'Final URL', 'Content Type', 'Timestamp']
                    writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
                    
                    writer.writeheader()
                    for result in failed_links:
                        content_type = "PDF" if result.is_pdf else "Image" if result.is_image else "Web"
                        writer.writerow({
                            'Status Code': result.status_code,
                            'URL': result.url,
                            'Error Message': result.message,
                            'Source File': result.source_file,
                            'Final URL': result.final_url if result.final_url != result.url else '',
                            'Content Type': content_type,
                            'Timestamp': result.timestamp
                        })
                
                print(f"CSV report for broken links saved: {filepath} ({len(failed_links)} entries)")
            else:
                print("No broken links found - CSV report not generated")
                
        except Exception as e:
            print(f"Error generating CSV report: {e}")

    async def generate_html_report(self, filepath: Path):
        """Generate enhanced HTML report with filtering"""
        try:
            failed = [r for r in self.results if r.status_code >= 400]
            successful = [r for r in self.results if r.status_code < 400]
            pdfs = [r for r in self.results if r.is_pdf]
            images = [r for r in self.results if r.is_image]
            timeouts = [r for r in self.results if r.status_code == 408]
            rate_limited = [r for r in self.results if r.status_code == 429]
            bot_blocked = [r for r in self.results if r.status_code == 999]
            forbidden = [r for r in self.results if r.status_code == 403]
            not_found = [r for r in self.results if r.status_code == 404]
            
            # Sort results by status code, then by URL
            sorted_results = sorted(self.results, key=lambda x: (x.status_code, x.url.lower()))
            
            html_content = f"""
<!DOCTYPE html>
<html>
<head>
    <title>Link Check Report</title>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        body {{ 
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; 
            margin: 0; 
            padding: 20px; 
            background-color: #f8f9fa; 
            line-height: 1.6;
        }}
        .container {{ 
            max-width: 1400px; 
            margin: 0 auto; 
            background: white; 
            padding: 30px; 
            border-radius: 12px; 
            box-shadow: 0 4px 6px rgba(0,0,0,0.1); 
        }}
        .header {{ 
            text-align: center; 
            margin-bottom: 40px; 
            border-bottom: 2px solid #e9ecef;
            padding-bottom: 20px;
        }}
        .header h1 {{ 
            color: #2c3e50; 
            margin: 0; 
            font-size: 2.5rem; 
        }}
        .header p {{ 
            color: #6c757d; 
            margin: 10px 0 0 0; 
            font-size: 1.1rem; 
        }}
        .summary {{ 
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); 
            color: white; 
            padding: 30px; 
            border-radius: 12px; 
            margin-bottom: 40px; 
        }}
        .summary h2 {{ 
            margin: 0 0 20px 0; 
            text-align: center; 
            font-size: 1.8rem; 
        }}
        .stats {{ 
            display: grid; 
            grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); 
            gap: 20px; 
            text-align: center; 
        }}
        .stat {{ 
            background: rgba(255,255,255,0.1); 
            padding: 20px; 
            border-radius: 8px; 
            backdrop-filter: blur(10px); 
        }}
        .stat h3 {{ 
            margin: 0; 
            font-size: 2.2rem; 
            font-weight: bold; 
        }}
        .stat p {{ 
            margin: 8px 0 0 0; 
            font-size: 0.9rem; 
            opacity: 0.9; 
        }}
        .filter-section {{
            margin-bottom: 20px;
            text-align: center;
        }}
        .filter-btn {{
            padding: 8px 16px;
            margin: 0 5px;
            border: 1px solid #007bff;
            background: white;
            color: #007bff;
            border-radius: 20px;
            cursor: pointer;
            font-size: 0.9rem;
        }}
        .filter-btn:hover {{
            background: #007bff;
            color: white;
        }}
        .filter-btn.active {{
            background: #007bff;
            color: white;
        }}
        .table-container {{ 
            overflow-x: auto; 
            margin-top: 30px;
            border-radius: 8px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }}
        table {{ 
            border-collapse: collapse; 
            width: 100%; 
            min-width: 800px;
            background: white;
        }}
        th {{ 
            background: linear-gradient(135deg, #2c3e50 0%, #34495e 100%); 
            color: white; 
            padding: 15px 12px; 
            text-align: left; 
            font-weight: 600; 
            font-size: 0.9rem;
            position: sticky;
            top: 0;
            z-index: 10;
        }}
        td {{ 
            padding: 12px; 
            border-bottom: 1px solid #e9ecef; 
            vertical-align: middle; 
        }}
        tr:hover {{ 
            background-color: #f8f9fa; 
        }}
        .url-cell {{ 
            max-width: 400px; 
            word-break: break-all; 
            font-family: Monaco, 'Courier New', monospace; 
            font-size: 0.85rem; 
        }}
        .url-cell a {{ 
            color: #007bff; 
            text-decoration: none; 
        }}
        .url-cell a:hover {{ 
            text-decoration: underline; 
        }}
        .source-cell {{ 
            max-width: 200px; 
            word-break: break-word; 
            font-size: 0.85rem; 
            color: #6c757d; 
        }}
        .message-cell {{ 
            max-width: 150px; 
            word-break: break-word; 
            font-size: 0.85rem; 
        }}
        .status-badge {{ 
            padding: 6px 10px; 
            border-radius: 20px; 
            font-weight: bold; 
            font-size: 0.8rem; 
            display: inline-block; 
            min-width: 45px; 
            text-align: center; 
        }}
        .status-200 {{ background: #d4edda; color: #155724; }}
        .status-301, .status-302 {{ background: #cce5ff; color: #004085; }}
        .status-403 {{ background: #fff3cd; color: #856404; }}
        .status-404 {{ background: #f5c6cb; color: #721c24; }}
        .status-408 {{ background: #d1ecf1; color: #0c5460; }}
        .status-429 {{ background: #ffeaa7; color: #856404; }}
        .status-500 {{ background: #f8d7da; color: #721c24; }}
        .status-999 {{ background: #e2e3e5; color: #383d41; }}
        .status-other {{ background: #e9ecef; color: #495057; }}
        .pdf-row {{ background-color: #fff3e0 !important; }}
        .image-row {{ background-color: #e8f5e8 !important; }}
        .footer {{ 
            text-align: center; 
            margin-top: 40px; 
            color: #6c757d; 
            font-size: 0.9rem; 
            border-top: 1px solid #e9ecef; 
            padding-top: 20px; 
        }}
        @media (max-width: 768px) {{
            .container {{ padding: 15px; }}
            .stats {{ grid-template-columns: repeat(2, 1fr); }}
            .url-cell {{ max-width: 200px; }}
            .source-cell {{ max-width: 120px; }}
        }}
    </style>
    <script>
        function filterTable(status) {{
            const rows = document.querySelectorAll('tbody tr');
            const buttons = document.querySelectorAll('.filter-btn');
            
            buttons.forEach(btn => btn.classList.remove('active'));
            event.target.classList.add('active');
            
            rows.forEach(row => {{
                if (status === 'all') {{
                    row.style.display = '';
                }} else if (status === 'failed') {{
                    const statusCode = parseInt(row.querySelector('.status-badge').textContent);
                    row.style.display = statusCode >= 400 ? '' : 'none';
                }} else {{
                    const hasClass = row.classList.contains('status-' + status);
                    row.style.display = hasClass ? '' : 'none';
                }}
            }});
        }}
    </script>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>Enhanced Link Check Report</h1>
            <p>Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
            <p>Total occurrences found: {getattr(self, 'total_occurrences', 'N/A')}</p>
        </div>
        
        <div class="summary">
            <h2>Comprehensive Summary</h2>
            <div class="stats">
                <div class="stat">
                    <h3>{len(successful)}</h3>
                    <p>Successful</p>
                </div>
                <div class="stat">
                    <h3>{len(failed)}</h3>
                    <p>Failed</p>
                </div>
                <div class="stat">
                    <h3>{len(pdfs)}</h3>
                    <p>PDFs</p>
                </div>
                <div class="stat">
                    <h3>{len(images)}</h3>
                    <p>Images</p>
                </div>
                <div class="stat">
                    <h3>{len(timeouts)}</h3>
                    <p>Timeouts</p>
                </div>
                <div class="stat">
                    <h3>{len(rate_limited)}</h3>
                    <p>Rate Limited</p>
                </div>
                <div class="stat">
                    <h3>{len(not_found)}</h3>
                    <p>Not Found</p>
                </div>
                <div class="stat">
                    <h3>{len(bot_blocked)}</h3>
                    <p>Bot Blocked</p>
                </div>
            </div>
        </div>

        <div class="filter-section">
            <button class="filter-btn active" onclick="filterTable('all')">All Links</button>
            <button class="filter-btn" onclick="filterTable('failed')">Failed Only</button>
            <button class="filter-btn" onclick="filterTable('404')">404 Not Found</button>
            <button class="filter-btn" onclick="filterTable('403')">403 Forbidden</button>
            <button class="filter-btn" onclick="filterTable('429')">429 Rate Limited</button>
            <button class="filter-btn" onclick="filterTable('408')">408 Timeout</button>
            <button class="filter-btn" onclick="filterTable('999')">999 Bot Blocked</button>
        </div>

        <div class="table-container">
            <table>
                <thead>
                    <tr>
                        <th style="width: 80px;">Status</th>
                        <th style="width: 400px;">URL</th>
                        <th style="width: 200px;">Source Files</th>
                        <th style="width: 150px;">Message</th>
                        <th style="width: 120px;">Type</th>
                        <th style="width: 300px;">Final URL</th>
                    </tr>
                </thead>
                <tbody>
"""
            
            for result in sorted_results:
                # Determine status class
                status_class = f"status-{result.status_code}" if result.status_code in [200, 301, 302, 403, 404, 408, 429, 500, 999] else "status-other"
                
                # Add category classes
                row_classes = [status_class]
                if result.status_code >= 400:
                    row_classes.append("failed")
                if result.is_pdf:
                    row_classes.append("pdf-row")
                elif result.is_image:
                    row_classes.append("image-row")
                
                # Determine badge style
                if result.status_code < 400:
                    badge_class = f"status-{result.status_code}" if result.status_code in [200, 301, 302] else "status-200"
                elif result.status_code == 403:
                    badge_class = "status-403"
                elif result.status_code == 404:
                    badge_class = "status-404"
                elif result.status_code == 408:
                    badge_class = "status-408"
                elif result.status_code == 429:
                    badge_class = "status-429"
                elif result.status_code >= 500:
                    badge_class = "status-500"
                elif result.status_code == 999:
                    badge_class = "status-999"
                else:
                    badge_class = "status-other"
                
                # Determine content type
                if result.is_pdf:
                    content_type = "PDF"
                elif result.is_image:
                    content_type = "Image"
                else:
                    content_type = "Web"
                
                # Format final URL
                final_url_display = ""
                if result.final_url != result.url:
                    final_url_display = result.final_url
                else:
                    final_url_display = "Same as original"
                
                html_content += f"""
                <tr class="{' '.join(row_classes)}">
                    <td><span class="status-badge {badge_class}">{result.status_code}</span></td>
                    <td class="url-cell"><a href="{result.url}" target="_blank" rel="noopener">{result.url}</a></td>
                    <td class="source-cell">{result.source_file}</td>
                    <td class="message-cell">{result.message}</td>
                    <td style="text-align: center;">{content_type}</td>
                    <td class="url-cell" style="font-size: 0.8rem; color: #6c757d;">{final_url_display}</td>
                </tr>"""

            html_content += f"""
                </tbody>
            </table>
        </div>
        
        <div class="footer">
            <p><strong>Enhanced Link Checker Report</strong></p>
            <p>{len(self.results)} unique links checked from {getattr(self, 'total_occurrences', 'N/A')} total occurrences</p>
            <p>Success Rate: {len(successful)/len(self.results)*100:.1f}%</p>
        </div>
    </div>
</body>
</html>"""

            async with aiofiles.open(filepath, 'w', encoding='utf-8') as f:
                await f.write(html_content)
            print(f"Enhanced HTML report saved: {filepath}")
        except Exception as e:
            print(f"Error generating HTML report: {e}")
            traceback.print_exc()

    def print_summary(self):
        """Print enhanced summary"""
        failed = [r for r in self.results if r.status_code >= 400]
        successful = [r for r in self.results if r.status_code < 400]
        pdfs = [r for r in self.results if r.is_pdf]
        images = [r for r in self.results if r.is_image]
        timeouts = [r for r in self.results if r.status_code == 408]
        rate_limited = [r for r in self.results if r.status_code == 429]
        bot_blocked = [r for r in self.results if r.status_code == 999]
        forbidden = [r for r in self.results if r.status_code == 403]
        not_found = [r for r in self.results if r.status_code == 404]
        
        print(f"\n" + "="*60)
        print(f"LINK CHECK SUMMARY")
        print(f"="*60)
        print(f"Total links checked: {len(self.results)}")
        print(f"Successful: {len(successful)} ({len(successful)/len(self.results)*100:.1f}%)")
        print(f"Failed: {len(failed)} ({len(failed)/len(self.results)*100:.1f}%)")
        print(f"PDFs: {len(pdfs)}")
        print(f"Images: {len(images)}")
        
        if timeouts:
            print(f"Timeouts: {len(timeouts)}")
        if rate_limited:
            print(f"Rate limited: {len(rate_limited)}")
        if bot_blocked:
            print(f"Bot blocked: {len(bot_blocked)}")
        if forbidden:
            print(f"Forbidden (403): {len(forbidden)}")
        if not_found:
            print(f"Not found (404): {len(not_found)}")
        
        if failed:
            print(f"\nFailed links by status code:")
            failure_groups = {}
            for result in failed:
                if result.status_code not in failure_groups:
                    failure_groups[result.status_code] = []
                failure_groups[result.status_code].append(result)
            
            for status_code in sorted(failure_groups.keys()):
                results = failure_groups[status_code]
                print(f"  {status_code}: {len(results)} links")
                for result in results[:3]:
                    print(f"    - {result.url}")
                if len(results) > 3:
                    print(f"    ... and {len(results) - 3} more")
        
        print(f"\nReports saved in 'reports/' directory")

async def main():
    if len(sys.argv) < 2 or len(sys.argv) > 3:
        print("Link Checker for Markdown Files")
        print("Usage: python link_checker.py <directory> [--debug]")
        print("Example: python link_checker.py ./docs")
        print("Example: python link_checker.py ./docs --debug")
        sys.exit(1)
    
    directory = sys.argv[1]
    debug = len(sys.argv) == 3 and sys.argv[2] == '--debug'
    
    checker = SimpleLinkChecker(debug=debug)
    await checker.check_directory(directory)

if __name__ == "__main__":
    asyncio.run(main())
