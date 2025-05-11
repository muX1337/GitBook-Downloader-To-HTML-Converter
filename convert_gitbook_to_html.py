import sys
import os
import requests
import json
import hashlib
import re
from bs4 import BeautifulSoup
from urllib.parse import urlparse, urljoin
from datetime import datetime

def get_cache_info(cache_file):
    """Load or initialize the cache with ETag, Last-Modified and MD5 hashes of pages"""
    if os.path.exists(cache_file):
        try:
            with open(cache_file, 'r') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            print(f"Cache file error: {e}, creating new cache")
    return {}

def save_cache_info(cache_file, cache_data):
    """Save the cache data to a file"""
    with open(cache_file, 'w') as f:
        json.dump(cache_data, f, indent=2)

def clean_html_for_comparison(html_content):
    """
    Clean HTML content by removing or normalizing parts that change but don't affect content,
    like timestamps, session IDs, CSRF tokens, etc.
    """
    # Convert to lowercase for case-insensitive comparison
    content = html_content.lower()
    
    # Remove scripts which often contain dynamic content
    content = re.sub(r'<script\b[^<]*(?:(?!<\/script>)<[^<]*)*<\/script>', '', content)
    
    # Remove style tags
    content = re.sub(r'<style\b[^<]*(?:(?!<\/style>)<[^<]*)*<\/style>', '', content)
    
    # Remove HTML comments
    content = re.sub(r'<!--[\s\S]*?-->', '', content)
    
    # Remove META tags which often contain dynamic content
    content = re.sub(r'<meta\b[^>]*>', '', content)
    
    # Remove common dynamic attributes and their values
    content = re.sub(r'\s(data-\w+|id|class|style|aria-\w+|role)=["\'][^"\']*["\']', '', content)
    
    # Remove timestamp patterns (various formats)
    content = re.sub(r'\b\d{4}[-/]\d{2}[-/]\d{2}[t\s]?\d{2}:\d{2}:\d{2}', '', content)
    content = re.sub(r'\b\d{2}[-/]\d{2}[-/]\d{4}[t\s]?\d{2}:\d{2}:\d{2}', '', content)
    content = re.sub(r'\b\d{2}:\d{2}:\d{2}.\d+z?\b', '', content)
    
    # Remove version numbers
    content = re.sub(r'v\d+\.\d+(\.\d+)?', '', content)
    
    # Remove URLs with query parameters (often contain session IDs)
    content = re.sub(r'(https?://[^"\'&\s]+)\?[^"\'\s]+', r'\1', content)
    
    # Remove GitBook-specific dynamic elements
    content = re.sub(r'updated-at=["\'][^"\']*["\']', '', content)
    content = re.sub(r'gitbook-\w+=["\'][^"\']*["\']', '', content)
    
    # Remove any data-* attributes with JSON content
    content = re.sub(r'data-\w+=[\'\"][{\[].*?[}\]][\'\"]', '', content)
    
    # Remove UUIDs and other long hex strings
    content = re.sub(r'[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}', '', content)
    content = re.sub(r'[a-f0-9]{32}', '', content) 
    
    # Remove all attributes from html and body tags
    content = re.sub(r'<html\s+[^>]*>', '<html>', content)
    content = re.sub(r'<body\s+[^>]*>', '<body>', content)
    
    # Remove whitespace variations
    content = re.sub(r'\s+', ' ', content)
    content = content.strip()
    
    return content

def calculate_md5(content):
    """Calculate MD5 hash of content for additional change detection"""
    # Clean the HTML content for more reliable comparison
    cleaned_content = clean_html_for_comparison(content)
    return hashlib.md5(cleaned_content.encode('utf-8')).hexdigest()

def get_page_title(html_content):
    """Extract the title from HTML content"""
    soup = BeautifulSoup(html_content, 'html.parser')
    title_tag = soup.find('title')
    if title_tag:
        return title_tag.get_text().strip()
    return None

def check_title_exists(title, title_mapping):
    """Check if a page with the same title already exists"""
    if not title_mapping or not title:
        return False  # No mapping provided or no title to check
    
    return title in title_mapping

def save_webpage_as_html(url, output_filename, cache_data, debug=False, title_mapping=None):
    """Download and save a webpage, but only if it has changed since last download and title matches patterns"""
    try:
        # Add a user agent to mimic a real browser
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'Cache-Control': 'max-age=0'
        }
        
        # Add conditional headers if we have cached data
        if url in cache_data:
            if 'etag' in cache_data[url]:
                headers['If-None-Match'] = cache_data[url]['etag']
                if debug:
                    print(f"Using ETag: {cache_data[url]['etag']} for {url}")
            if 'last_modified' in cache_data[url]:
                headers['If-Modified-Since'] = cache_data[url]['last_modified']
                if debug:
                    print(f"Using Last-Modified: {cache_data[url]['last_modified']} for {url}")

        # Send a GET request to the URL to fetch the content
        session = requests.Session()
        response = session.get(url, headers=headers)
        
        # If we get a 304 Not Modified status, the content hasn't changed
        if response.status_code == 304:
            print(f"No changes for {url} - skipping download (304 Not Modified)")
            return False
            
        response.raise_for_status()  # Raise an exception for other 4xx and 5xx status codes
        
        # Extract page title and check if it already exists
        page_title = get_page_title(response.text)
        if page_title:
            if title_mapping and check_title_exists(page_title, title_mapping):
                existing_file = title_mapping[page_title]
                print(f"Skipping {url} - page with title '{page_title}' already exists in {existing_file}")
                
                # Store title in cache for future reference
                if url not in cache_data:
                    cache_data[url] = {}
                cache_data[url]['title'] = page_title
                
                return False
            
            # Store title in cache
            if url not in cache_data:
                cache_data[url] = {}
            cache_data[url]['title'] = page_title
            
            # Add to title mapping if we're checking for duplicates
            if title_mapping is not None:
                title_mapping[page_title] = os.path.basename(output_filename)
            
            if debug:
                print(f"Page title for {url}: {page_title}")
        
        # Debug response headers
        if debug:
            print(f"Response headers for {url}:")
            for header, value in response.headers.items():
                print(f"  {header}: {value}")
        
        # Calculate MD5 of the cleaned content
        content_md5 = calculate_md5(response.text)
        
        # Create a flag to track if file actually has new content
        content_changed = True
        
        # Check if we have a cached MD5 for this URL
        if url in cache_data and 'md5' in cache_data[url]:
            if cache_data[url]['md5'] == content_md5:
                print(f"Content unchanged for {url} (verified by MD5) - skipping save")
                content_changed = False
            elif debug:
                print(f"MD5 changed for {url}: {cache_data[url]['md5']} -> {content_md5}")
        
        # Even if MD5 has changed, check if the file exists and content is functionally the same
        if content_changed and os.path.exists(output_filename):
            try:
                with open(output_filename, "r", encoding="utf-8") as html_file:
                    existing_content = html_file.read()
                existing_md5 = calculate_md5(existing_content)
                
                if existing_md5 == content_md5:
                    print(f"File content unchanged for {url} - skipping save")
                    content_changed = False
                elif debug:
                    # If MD5 changed but we're still downloading, let's see the differences more clearly
                    clean_existing = clean_html_for_comparison(existing_content)
                    clean_new = clean_html_for_comparison(response.text)
                    
                    if len(clean_existing) == len(clean_new):
                        print(f"Content lengths match: {len(clean_existing)} chars")
                    else:
                        print(f"Content length changed: {len(clean_existing)} -> {len(clean_new)}")
                    
                    # Compare and find differences (sample)
                    if debug and len(clean_existing) > 0 and len(clean_new) > 0:
                        # Find a few different chunks for debugging
                        for i in range(0, min(len(clean_existing), len(clean_new)), len(clean_existing)//10):
                            end = min(i + 100, len(clean_existing), len(clean_new))
                            if clean_existing[i:end] != clean_new[i:end]:
                                print(f"Difference at position {i}:")
                                print(f"Old: {clean_existing[i:end]}")
                                print(f"New: {clean_new[i:end]}")
                                break
            except Exception as e:
                if debug:
                    print(f"Error comparing existing file: {e}")
        
        # Update cache info even if content hasn't changed
        if not content_changed:
            if url not in cache_data:
                cache_data[url] = {}
            
            cache_data[url]['md5'] = content_md5
            if 'ETag' in response.headers:
                cache_data[url]['etag'] = response.headers['ETag']
            if 'Last-Modified' in response.headers:
                cache_data[url]['last_modified'] = response.headers['Last-Modified']
            return False
            
        # Content has changed or is new, save it
        with open(output_filename, "w", encoding="utf-8") as html_file:
            html_file.write(response.text)
            
        # Update cache with the new ETag, Last-Modified and MD5
        if url not in cache_data:
            cache_data[url] = {}
            
        cache_data[url]['md5'] = content_md5
        cache_data[url]['last_download'] = datetime.now().isoformat()
        
        if 'ETag' in response.headers:
            cache_data[url]['etag'] = response.headers['ETag']
            
        if 'Last-Modified' in response.headers:
            cache_data[url]['last_modified'] = response.headers['Last-Modified']
            
        print(f"Web page saved as {output_filename}")
        return True
        
    except requests.exceptions.RequestException as e:
        print(f"An error occurred downloading {url}: {e}")
        return False
    except Exception as e:
        print(f"Unexpected error with {url}: {e}")
        return False

def convert_gitbook_to_html(base_url, output_directory, debug=False, force_download=False, ignore_patterns=None, check_title_duplicate=False):
    try:
        # Create the output directory if it doesn't exist
        os.makedirs(output_directory, exist_ok=True)
        
        # Create cache directory if it doesn't exist
        cache_dir = os.path.join(output_directory, '.cache')
        os.makedirs(cache_dir, exist_ok=True)
        
        # Initialize cache
        cache_file = os.path.join(cache_dir, 'download_cache.json')
        cache_data = get_cache_info(cache_file)
        
        # Create title mapping for duplicate checking
        title_mapping = {}
        if check_title_duplicate:
            # Scan existing files for their titles to build the mapping
            for filename in os.listdir(output_directory):
                if filename.endswith('.html'):
                    file_path = os.path.join(output_directory, filename)
                    try:
                        with open(file_path, 'r', encoding='utf-8') as f:
                            content = f.read()
                            title = get_page_title(content)
                            if title:
                                title_mapping[title] = filename
                                if debug:
                                    print(f"Found existing title: '{title}' in {filename}")
                    except Exception as e:
                        if debug:
                            print(f"Error reading file {filename}: {e}")
        
        # Default ignore patterns for URLs
        if ignore_patterns is None:
            ignore_patterns = []
            
        # Download main page
        main_page_output = os.path.join(output_directory, 'index.html')
        saved_main = save_webpage_as_html(base_url, main_page_output, cache_data, debug, title_mapping if check_title_duplicate else None)
        
        # Use the cached version if it exists and there were no changes
        if saved_main or force_download:
            response = requests.get(base_url)
            response.raise_for_status()
            html_content = response.text
        else:
            try:
                with open(main_page_output, 'r', encoding='utf-8') as f:
                    html_content = f.read()
            except Exception as e:
                if debug:
                    print(f"Couldn't read cached main page, downloading again: {e}")
                response = requests.get(base_url)
                response.raise_for_status()
                html_content = response.text
        
        # Parse the HTML content
        soup = BeautifulSoup(html_content, 'html.parser')
        
        # Find all links on the page
        links = soup.find_all('a')
        
        # Iterate through the links
        processed_urls = set()  # To avoid processing duplicate URLs
        
        # Create a temporary cache file to write to during this run
        temp_cache_file = os.path.join(cache_dir, 'temp_cache.json')
        
        # Save cache periodically
        page_count = 0
        
        for link in links:
            href = link.get('href')
            if href and href.startswith('/'):  # Check if the link is relative
                full_url = urljoin(base_url, href)  # Make it an absolute URL
                
                # Skip if already processed
                if full_url in processed_urls:
                    continue
                processed_urls.add(full_url)
                
                # Skip if URL matches any ignore pattern
                if any(re.search(pattern, full_url) for pattern in ignore_patterns):
                    if debug:
                        print(f"Skipping ignored URL: {full_url}")
                    continue
                
                parsed_url = urlparse(full_url)
                # Create a safe filename from the URL path
                safe_path = parsed_url.path.lstrip('/')
                if not safe_path:
                    safe_path = 'index'
                
                # Replace slashes with underscores and ensure we have a valid filename
                safe_filename = safe_path.replace('/', '_')
                # Make sure the filename is not too long
                if len(safe_filename) > 100:
                    # Create a hash of the path to ensure uniqueness
                    path_hash = hashlib.md5(safe_path.encode()).hexdigest()[:10]
                    safe_filename = safe_filename[:90] + '_' + path_hash
                
                output_filename = os.path.join(output_directory, safe_filename + '.html')
                
                # Download and save the linked page as an HTML file if it has changed
                save_webpage_as_html(full_url, output_filename, cache_data, debug, title_mapping if check_title_duplicate else None)
                
                # Increment page count and periodically save cache to avoid losing progress
                page_count += 1
                if page_count % 10 == 0:
                    save_cache_info(temp_cache_file, cache_data)
        
        # Save the final updated cache
        save_cache_info(cache_file, cache_data)
        
        # Remove the temporary cache file if it exists
        if os.path.exists(temp_cache_file):
            os.remove(temp_cache_file)
            
        print(f"Processed {page_count} pages.")
        
    except requests.exceptions.RequestException as e:
        print(f"An error occurred: {e}")
    except Exception as e:
        print(f"Unexpected error: {e}")

def main():
    import argparse
    parser = argparse.ArgumentParser(description='Download a GitBook site to HTML files')
    parser.add_argument('gitbook_url', help='URL of the GitBook site')
    parser.add_argument('output_directory', help='Directory to save HTML files')
    parser.add_argument('--debug', action='store_true', help='Enable debug logging')
    parser.add_argument('--force', action='store_true', help='Force download even if content has not changed')
    parser.add_argument('--ignore', nargs='*', default=[], help='Regex patterns for URLs to ignore')
    parser.add_argument('--check-title-duplicate', action='store_true',
                      help='Check if a page with same title already exists and skip download if so')
    parser.add_argument('--list-titles', action='store_true',
                      help='Only list page titles without downloading content')
    
    args = parser.parse_args()
    
    print(f"Starting download of {args.gitbook_url} to {args.output_directory}")
    print(f"Debug mode: {args.debug}")
    print(f"Force download: {args.force}")
    
    if args.ignore:
        print(f"Ignoring URLs matching patterns: {args.ignore}")
        
    if args.check_title_duplicate:
        print("Checking for duplicate titles to avoid re-downloading content")
        
    if args.list_titles:
        print("Title listing mode enabled - pages will not be downloaded")
        # In this mode, we'll just collect titles and print them
        temp_cache_dir = os.path.join(args.output_directory, '.cache-temp')
        os.makedirs(temp_cache_dir, exist_ok=True)
        
        # Initialize a temporary cache for title listing
        temp_cache = {}
        
        try:
            # Just get the main page to start
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }
            response = requests.get(args.gitbook_url, headers=headers)
            response.raise_for_status()
            
            # Extract and print the main page title
            main_title = get_page_title(response.text)
            if main_title:
                print(f"Main page title: {main_title}")
            
            # Parse links from the main page
            soup = BeautifulSoup(response.text, 'html.parser')
            links = soup.find_all('a')
            
            processed_urls = set()
            title_count = 0
            
            print("\nCollecting page titles...")
            
            # Process each link to get titles
            for link in links:
                href = link.get('href')
                if href and href.startswith('/'):
                    full_url = urljoin(args.gitbook_url, href)
                    
                    # Skip if already processed
                    if full_url in processed_urls:
                        continue
                    processed_urls.add(full_url)
                    
                    # Skip if URL matches any ignore pattern
                    if any(re.search(pattern, full_url) for pattern in args.ignore):
                        if args.debug:
                            print(f"Skipping ignored URL: {full_url}")
                        continue
                    
                    try:
                        # Get the page
                        page_response = requests.get(full_url, headers=headers)
                        page_response.raise_for_status()
                        
                        # Extract title
                        page_title = get_page_title(page_response.text)
                        if page_title:
                            print(f"[{title_count}] URL: {full_url}")
                            print(f"    Title: {page_title}")
                            
                            # Store in temporary cache
                            temp_cache[full_url] = {'title': page_title}
                            title_count += 1
                        
                    except Exception as e:
                        print(f"Error getting title for {full_url}: {e}")
            
            print(f"\nFound {title_count} page titles.")
            
            # Save the titles to a file for reference
            titles_file = os.path.join(args.output_directory, 'page_titles.json')
            with open(titles_file, 'w') as f:
                json.dump(temp_cache, f, indent=2)
            print(f"Titles saved to {titles_file}")
            
        except Exception as e:
            print(f"Error in title listing mode: {e}")
            
    else:
        # Normal download mode
        convert_gitbook_to_html(
            args.gitbook_url, 
            args.output_directory, 
            debug=args.debug,
            force_download=args.force,
            ignore_patterns=args.ignore,
            check_title_duplicate=args.check_title_duplicate
        )

if __name__ == "__main__":
    main()