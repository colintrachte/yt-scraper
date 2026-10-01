from googleapiclient.discovery import build
import pandas as pd
import yaml
from datetime import datetime
import gradio as gr
import os
import re
import requests
import time
from urllib.parse import urlparse
from autocomplete_apis import create_autocomplete_manager

# Default API key (can be overridden through the interface)
DEFAULT_API_KEY = 'ghhjhb'

# --- Keyword expansion helpers ---
def expand_keyword_base_forms(keyword):
    variants = set()
    k = keyword.strip()
    if not k:
        return []
    variants.add(k)
    # Plural/singular naive variants
    if not k.endswith('s'):
        variants.add(f"{k}s")
    if k.endswith('y'):
        variants.add(k[:-1] + 'ies')
    if k.endswith('es'):
        variants.add(k[:-2])
    # Common modifiers
    modifiers = ['official', 'remix', 'mix', 'live', 'tutorial', 'best', 'top', '2024', 'trending']
    for m in modifiers:
        variants.add(f"{k} {m}")
    return list(variants)

def fetch_suggested_keywords(seed, region_code):
    try:
        # YouTube suggestion service
        resp = requests.get(
            'https://suggestqueries.google.com/complete/search',
            params={'client': 'youtube', 'ds': 'yt', 'hl': region_code, 'q': seed},
            timeout=8
        )
        text = resp.text
        # The service can return JSON/JSONP; extract quoted suggestions
        suggestions = re.findall(r'"(.*?)"', text)
        # First element is usually the echo of the query; keep non-identical
        cleaned = [s for s in suggestions if s.lower() != seed.lower()]
        return cleaned[:20]
    except:
        return []

def generate_expanded_keywords(seed_keyword, region_code, use_trending, youtube, max_expanded, use_enhanced_autocomplete=True, cache_duration=24, max_requests_per_minute=10, use_alternative_sources=True, fallback_to_static=True):
    """Enhanced keyword expansion with YouTube autocomplete integration"""
    if use_enhanced_autocomplete:
        try:
            # Create autocomplete manager with UI config
            autocomplete_config = {
                'enabled': True,
                'max_suggestions': 20,
                'max_long_tail_variations': 15,
                'include_long_tail': True,
                'cache_duration_hours': cache_duration,
                'max_requests_per_minute': max_requests_per_minute,
                'use_alternative_sources': use_alternative_sources,
                'fallback_to_static': fallback_to_static
            }
            autocomplete_manager = create_autocomplete_manager(autocomplete_config)
            
            # Get enhanced suggestions
            enhanced_suggestions = autocomplete_manager.get_comprehensive_youtube_suggestions(
                seed_keyword, region_code, max_results=max_expanded
            )
            
            # Combine with base forms
            base = expand_keyword_base_forms(seed_keyword)
            expanded = list({*base, *enhanced_suggestions})
            
            if use_trending:
                trending = get_trending_topics(youtube, region_code)
                expanded.extend(trending)
            
            # Deduplicate and cap
            seen = []
            for k in expanded:
                if k and k not in seen:
                    seen.append(k)
                if len(seen) >= max_expanded:
                    break
            return seen if seen else [seed_keyword]
            
        except Exception as e:
            print(f"Enhanced autocomplete failed, falling back to legacy method: {e}")
            # Fallback to legacy method
            pass
    
    # Legacy method (original implementation)
    base = expand_keyword_base_forms(seed_keyword)
    suggested = fetch_suggested_keywords(seed_keyword, region_code)
    expanded = list({*base, *suggested})
    if use_trending:
        trending = get_trending_topics(youtube, region_code)
        expanded.extend(trending)
    # Deduplicate and cap
    seen = []
    for k in expanded:
        if k and k not in seen:
            seen.append(k)
        if len(seen) >= max_expanded:
            break
    return seen if seen else [seed_keyword]

# --- API key rotation helpers ---
def build_youtube_client(api_key):
    return build('youtube', 'v3', developerKey=api_key.strip())

def execute_with_key_rotation(request_builder_fn, api_keys):
    last_error = None
    for key in api_keys:
        try:
            youtube = build_youtube_client(key)
            req = request_builder_fn(youtube)
            return req.execute(), youtube
        except Exception as e:
            err = str(e).lower()
            last_error = e
            if 'quotaexceeded' in err or 'forbidden' in err or 'apikeyinvalid' in err:
                continue
            raise
    if last_error is not None:
        raise last_error
    raise RuntimeError('No API keys configured')

def extract_social_media_links(description):
    """Extract social media links from channel description"""
    social_links = {
        'twitter': [],
        'instagram': [],
        'facebook': [],
        'tiktok': [],
        'website': []
    }
    
    if not description:
        return social_links
    
    # Social media patterns
    patterns = {
        'twitter': [
            r'(?:https?://)?(?:www\.)?twitter\.com/([a-zA-Z0-9_]+)',
            r'(?:https?://)?(?:www\.)?x\.com/([a-zA-Z0-9_]+)',
            r'@([a-zA-Z0-9_]+)(?:\s|$)'
        ],
        'instagram': [
            r'(?:https?://)?(?:www\.)?instagram\.com/([a-zA-Z0-9_.]+)',
            r'(?:https?://)?(?:www\.)?instagr\.am/([a-zA-Z0-9_.]+)'
        ],
        'facebook': [
            r'(?:https?://)?(?:www\.)?facebook\.com/([a-zA-Z0-9.]+)',
            r'(?:https?://)?(?:www\.)?fb\.com/([a-zA-Z0-9.]+)'
        ],
        'tiktok': [
            r'(?:https?://)?(?:www\.)?tiktok\.com/@([a-zA-Z0-9_.]+)'
        ]
    }
    
    # Extract social media links
    for platform, pattern_list in patterns.items():
        for pattern in pattern_list:
            matches = re.findall(pattern, description, re.IGNORECASE)
            social_links[platform].extend(matches)
    
    # Extract general websites
    website_pattern = r'(?:https?://)?(?:www\.)?([a-zA-Z0-9.-]+\.[a-zA-Z]{2,})'
    websites = re.findall(website_pattern, description, re.IGNORECASE)
    
    # Filter out social media domains from websites
    social_domains = ['twitter.com', 'x.com', 'instagram.com', 'facebook.com', 'tiktok.com', 'youtube.com', 'youtu.be']
    filtered_websites = [w for w in websites if not any(domain in w.lower() for domain in social_domains)]
    social_links['website'] = list(set(filtered_websites))
    
    # Remove duplicates and clean up
    for platform in social_links:
        social_links[platform] = list(set(social_links[platform]))
    
    return social_links

def extract_contact_info(description):
    """Extract contact information from channel description"""
    contact_info = {
        'emails': [],
        'phone_numbers': [],
        'business_inquiries': []
    }
    
    if not description:
        return contact_info
    
    # Email patterns
    email_pattern = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
    emails = re.findall(email_pattern, description)
    contact_info['emails'] = list(set(emails))
    
    # Phone number patterns (various formats)
    phone_patterns = [
        r'\+?1?[-.\s]?\(?([0-9]{3})\)?[-.\s]?([0-9]{3})[-.\s]?([0-9]{4})',  # US format
        r'\+?([0-9]{1,4})[-.\s]?([0-9]{3,4})[-.\s]?([0-9]{3,4})[-.\s]?([0-9]{3,4})',  # International
        r'\b\d{3}[-.]?\d{3}[-.]?\d{4}\b'  # Simple format
    ]
    
    for pattern in phone_patterns:
        matches = re.findall(pattern, description)
        if matches:
            contact_info['phone_numbers'].extend(['-'.join(match) if isinstance(match, tuple) else match for match in matches])
    
    # Business inquiry keywords
    business_keywords = [
        'business inquiries', 'business inquiry', 'business email', 'contact',
        'collaboration', 'sponsorship', 'partnership', 'booking', 'management'
    ]
    
    for keyword in business_keywords:
        if keyword.lower() in description.lower():
            # Try to find email near business keywords
            keyword_index = description.lower().find(keyword.lower())
            surrounding_text = description[max(0, keyword_index-50):keyword_index+100]
            business_emails = re.findall(email_pattern, surrounding_text)
            contact_info['business_inquiries'].extend(business_emails)
    
    # Remove duplicates
    for key in contact_info:
        contact_info[key] = list(set(contact_info[key]))
    
    return contact_info

def get_trending_topics(youtube, region_code='US', max_results=50):
    """Get trending topics from YouTube"""
    try:
        # Get trending videos
        request = youtube.videos().list(
            part='snippet',
            chart='mostPopular',
            regionCode=region_code,
            maxResults=max_results,
            videoCategoryId='0'  # All categories
        )
        response = request.execute()
        
        # Extract trending keywords from titles and tags
        trending_keywords = []
        for item in response.get('items', []):
            title = item['snippet']['title']
            tags = item['snippet'].get('tags', [])
            
            # Extract keywords from title (simple approach)
            title_words = re.findall(r'\b[A-Za-z]{3,}\b', title.lower())
            trending_keywords.extend(title_words)
            trending_keywords.extend([tag.lower() for tag in tags])
        
        # Count frequency and return top keywords
        from collections import Counter
        keyword_counts = Counter(trending_keywords)
        top_keywords = [keyword for keyword, count in keyword_counts.most_common(20)]
        
        return top_keywords
    except Exception as e:
        return ['music', 'gaming', 'entertainment', 'news', 'sports', 'technology', 'cooking', 'travel']

def bulk_keyword_search(keywords_list):
    """Process multiple keywords for bulk search"""
    if isinstance(keywords_list, str):
        # Split by comma, semicolon, or newline
        keywords = re.split(r'[,;\n]+', keywords_list)
        keywords = [k.strip() for k in keywords if k.strip()]
    else:
        keywords = keywords_list
    
    return keywords

def scrape_youtube_channels(
    api_key,
    searching_keyword,
    min_subscribers,
    max_subscribers,
    region_code,
    channel_views,
    videos_count,
    start_date,
    end_date,
    max_pages=20,
    bulk_keywords="",
    extract_social=True,
    extract_contact=True,
    use_trending=False,
    save_per_keyword=False,
    api_keys_text="",
    keyword_expansion=True,
    max_expanded_keywords=10,
    analyze_recent_videos=10,
    api_key_offset=0,
    use_enhanced_autocomplete=True,
    cache_duration=24,
    max_requests_per_minute=10,
    use_alternative_sources=True,
    fallback_to_static=True
):
    """
    Scrape YouTube channels based on the given parameters
    """
    try:
        # Validate API key
        if not api_key or api_key.strip() == "":
            return "❌ Error: Please provide a valid YouTube API key", "", "API key is required"
        
        # Prepare API keys list (UI field or single key)
        keys = []
        if api_keys_text and isinstance(api_keys_text, str):
            keys.extend([k.strip() for k in re.split(r'[\n,;]+', api_keys_text) if k.strip()])
        if api_key and api_key.strip():
            keys.append(api_key.strip())
        # Fallback to default
        if not keys:
            keys.append(DEFAULT_API_KEY)

        # Apply manual offset for key rotation
        if keys:
            try:
                offset = int(api_key_offset or 0)
                if offset != 0 and len(keys) > 1:
                    offset = offset % len(keys)
                    keys = keys[offset:] + keys[:offset]
            except:
                pass
        # Create initial client
        youtube = build('youtube', 'v3', developerKey=keys[0])
        
        # Handle bulk keywords or trending topics
        keywords_to_search = []
        
        if use_trending and not bulk_keywords.strip():
            trending_keywords = get_trending_topics(youtube, region_code)
            keywords_to_search.extend(trending_keywords[:10])
            progress_info = [f"Using trending topics: {', '.join(keywords_to_search[:5])}..."]
        elif bulk_keywords.strip():
            keywords_to_search = bulk_keyword_search(bulk_keywords)
            progress_info = [f"Bulk search with {len(keywords_to_search)} keywords: {', '.join(keywords_to_search[:3])}..."]
        else:
            if keyword_expansion:
                keywords_to_search = generate_expanded_keywords(
                    searching_keyword, region_code, use_trending, youtube, max_expanded_keywords, 
                    use_enhanced_autocomplete, cache_duration, max_requests_per_minute, 
                    use_alternative_sources, fallback_to_static
                )
                progress_info = [f"Enhanced expansion from '{searching_keyword}' → {len(keywords_to_search)} keywords"]
            else:
                keywords_to_search = [searching_keyword]
                progress_info = [f"Single keyword search: {searching_keyword}"]
        
        # Helper function to parse date from DD/MM/YYYY format
        def parse_date(date_str):
            try:
                return datetime.strptime(date_str, "%d/%m/%Y")
            except:
                return None

        # Helper function to parse YouTube date format
        def parse_youtube_date(date_str):
            try:
                return datetime.strptime(date_str[:10], "%Y-%m-%d")
            except:
                return None

        all_channels_info = []
        
        # Search for each keyword
        for keyword_idx, current_keyword in enumerate(keywords_to_search):
            progress_info.append(f"\\nSearching keyword {keyword_idx + 1}/{len(keywords_to_search)}: '{current_keyword}'")
            
            nextPageToken = ""
            keyword_channels = []
            
            for i in range(max_pages // max(1, len(keywords_to_search)) + 1):
                try:
                    def _build_req(yt):
                        return yt.search().list(
                            part="snippet",
                            order="relevance",
                            maxResults=50,
                            q=str(current_keyword),
                            pageToken=nextPageToken,
                            regionCode=region_code
                        )
                    response, youtube = execute_with_key_rotation(_build_req, keys)
                    
                    page_channels = 0
                    for item in response.get('items', []):
                        channelId = item['snippet']['channelId']
                        channel_title = item['snippet']['channelTitle']
                        channel_desc = {
                            "channelId": channelId, 
                            "channelTitle": channel_title, 
                            "ChannelLink": f"https://www.youtube.com/channel/{channelId}/"
                        }
                        
                        channel_stats = youtube.channels().list(
                            part=['statistics', 'snippet', 'contentDetails'],
                            id=channelId
                        ).execute()
                        
                        try:
                            country = channel_stats['items'][0]['snippet']["country"]
                        except:
                            country = ""
                        channel_desc.update({"country": country})
                        
                        # Get channel creation date
                        try:
                            published_at = channel_stats['items'][0]['snippet']["publishedAt"]
                            channel_desc.update({"publishedAt": published_at})
                        except:
                            channel_desc.update({"publishedAt": ""})
                        
                        # Get channel description for social media and contact extraction
                        try:
                            description = channel_stats['items'][0]['snippet'].get("description", "")
                            channel_desc.update({"description": description})
                            
                            # Extract social media links if enabled
                            if extract_social:
                                social_links = extract_social_media_links(description)
                                channel_desc.update({
                                    "twitter": "; ".join(social_links['twitter']),
                                    "instagram": "; ".join(social_links['instagram']),
                                    "facebook": "; ".join(social_links['facebook']),
                                    "tiktok": "; ".join(social_links['tiktok']),
                                    "websites": "; ".join(social_links['website'])
                                })
                            
                            # Extract contact information if enabled
                            if extract_contact:
                                contact_info = extract_contact_info(description)
                                channel_desc.update({
                                    "emails": "; ".join(contact_info['emails']),
                                    "phone_numbers": "; ".join(contact_info['phone_numbers']),
                                    "business_inquiries": "; ".join(contact_info['business_inquiries'])
                                })
                        except:
                            pass
                        
                        channel_stats['items'][0]['statistics']
                        stats_obj = channel_stats['items'][0]['statistics']
                        channel_desc.update(stats_obj)

                        # Additional metadata
                        try:
                            subs = int(stats_obj.get('subscriberCount', 0)) or 0
                            views = int(stats_obj.get('viewCount', 0)) or 0
                            channel_desc.update({
                                "engagement_rate": round((views / subs), 3) if subs > 0 else None
                            })
                        except:
                            pass

                        # Upload frequency and category from recent videos (optional analysis)
                        if analyze_recent_videos > 0:
                            try:
                                uploads_playlist = channel_stats['items'][0]['contentDetails']['relatedPlaylists']['uploads']
                                def _recent_req(yt):
                                    return yt.playlistItems().list(part='snippet,contentDetails', playlistId=uploads_playlist, maxResults=min(50, max(5, analyze_recent_videos)))
                                recent_resp, youtube = execute_with_key_rotation(_recent_req, keys)
                                items = recent_resp.get('items', [])
                                video_ids = [it['contentDetails']['videoId'] for it in items]
                                dates = [it['contentDetails']['videoPublishedAt'][:10] for it in items if 'videoPublishedAt' in it['contentDetails']]
                                if len(dates) >= 2:
                                    try:
                                        dts = [datetime.strptime(d, "%Y-%m-%d") for d in dates]
                                        dts.sort()
                                        total_days = (dts[-1] - dts[0]).days or 1
                                        freq = round(len(dts) / total_days, 3) if total_days > 0 else None
                                        channel_desc.update({"upload_frequency_per_day": freq})
                                    except:
                                        pass
                                # Fetch categories of recent videos
                                categories = []
                                if video_ids:
                                    def _videos_req(yt):
                                        return yt.videos().list(part='snippet', id=','.join(video_ids[:50]))
                                    vids_resp, youtube = execute_with_key_rotation(_videos_req, keys)
                                    for v in vids_resp.get('items', []):
                                        cat_id = v['snippet'].get('categoryId')
                                        if cat_id:
                                            categories.append(cat_id)
                                if categories:
                                    # Map categoryId to names
                                    def _cats_req(yt):
                                        return yt.videoCategories().list(part='snippet', regionCode=region_code)
                                    cats_resp, youtube = execute_with_key_rotation(_cats_req, keys)
                                    id_to_name = {c['id']: c['snippet']['title'] for c in cats_resp.get('items', [])}
                                    from collections import Counter
                                    top = Counter(categories).most_common(1)
                                    if top:
                                        channel_desc.update({
                                            "dominant_category_id": top[0][0],
                                            "dominant_category_name": id_to_name.get(top[0][0])
                                        })
                            except:
                                pass
                        channel_desc.update({"search_keyword": current_keyword})  # Track which keyword found this channel
                        keyword_channels.append(channel_desc)
                        page_channels += 1
                    
                    progress_info.append(f"Page {i+1}: Found {page_channels} channels")
                    nextPageToken = response.get("nextPageToken", "")
                    
                except KeyError:
                    progress_info.append(f"Completed scraping keyword '{current_keyword}' at page {i+1} (no more pages)")
                    break
                except Exception as e:
                    progress_info.append(f"Error on page {i+1} for keyword '{current_keyword}': {str(e)}")
                    break
            
            all_channels_info.extend(keyword_channels)
            progress_info.append(f"Keyword '{current_keyword}' completed: {len(keyword_channels)} channels found")

        if not all_channels_info:
            return "No channels found", "", "\n".join(progress_info)

        channels_df = pd.DataFrame(all_channels_info)
        channels_df = channels_df.astype({'viewCount': 'int64', 'subscriberCount': 'int64', 'videoCount': 'int64'})

        # Apply filters
        filter_conditions = (
            (channels_df.viewCount >= channel_views) & 
            (channels_df.subscriberCount >= min_subscribers) & 
            (channels_df.subscriberCount <= max_subscribers) & 
            (channels_df.videoCount >= videos_count) & 
            (channels_df.country == region_code)
        )

        # Add date range filtering if dates are provided
        if start_date and end_date:
            start_dt = parse_date(start_date)
            end_dt = parse_date(end_date)
            
            if start_dt and end_dt:
                # Convert publishedAt to datetime for comparison
                channels_df['publishedAt_dt'] = channels_df['publishedAt'].apply(parse_youtube_date)
                
                # Add date filter to conditions
                date_filter = (
                    (channels_df['publishedAt_dt'] >= start_dt) & 
                    (channels_df['publishedAt_dt'] <= end_dt) &
                    (channels_df['publishedAt_dt'].notna())
                )
                filter_conditions = filter_conditions & date_filter

        results = channels_df[filter_conditions].copy()
        
        # Clean up results
        if 'hiddenSubscriberCount' in results.columns:
            results.drop('hiddenSubscriberCount', inplace=True, axis=1)
        if 'publishedAt_dt' in results.columns:
            results.drop('publishedAt_dt', inplace=True, axis=1)
            
        results.drop_duplicates('channelId', inplace=True)

        # Create summary
        summary = f"""
🎯 Scraping Summary:
📊 Total channels found: {len(channels_df):,}
✅ Channels matching criteria: {len(results):,}
👥 Subscriber range: {min_subscribers:,} - {max_subscribers:,}
🌍 Region: {region_code}
📺 Min channel views: {channel_views:,}
🎬 Min videos: {videos_count}
"""
        
        if start_date and end_date:
            summary += f"📅 Date range: {start_date} - {end_date}\n"
        
        # Save to CSV with timestamp
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        if len(keywords_to_search) > 1 and save_per_keyword:
            # Save one file per keyword
            per_keyword_counts = {}
            for kw in keywords_to_search:
                kw_results = results[results['search_keyword'] == kw]
                if len(kw_results) == 0:
                    continue
                safe_kw = kw.replace(' ', '_')[:60]
                kw_filename = f"{safe_kw}_{timestamp}.csv"
                kw_results.to_csv(kw_filename, index=False)
                per_keyword_counts[kw] = len(kw_results)
            if per_keyword_counts:
                summary += "💾 Saved per-keyword files:" + "\n" + "\n".join([f"- {k}: {v} rows" for k, v in per_keyword_counts.items()])
            else:
                summary += "No per-keyword files saved (no results per keyword)."
        else:
            if len(keywords_to_search) > 1:
                filename = f"bulk_search_{len(keywords_to_search)}_keywords_results_{timestamp}.csv"
            elif use_trending:
                filename = f"trending_topics_{region_code}_results_{timestamp}.csv"
            else:
                filename = f"{searching_keyword.replace(' ', '_')}_{timestamp}.csv"
            results.to_csv(filename, index=False)
            summary += f"💾 Results saved to: {filename}"
        
        # Return results as HTML table for display
        if len(results) > 0:
            results_html = results.head(50).to_html(classes='table table-striped', escape=False)
            if len(results) > 50:
                results_html += f"<p><strong>Showing first 50 results out of {len(results)} total results.</strong></p>"
        else:
            results_html = "<p>No channels match the specified criteria.</p>"
        
        return summary, results_html, "\n".join(progress_info)
        
    except Exception as e:
        error_msg = str(e)
        if "API key" in error_msg or "quotaExceeded" in error_msg or "forbidden" in error_msg.lower():
            return "❌ API Key Error: Please check your YouTube API key. Make sure it's valid and has YouTube Data API v3 enabled.", "", f"API Error: {error_msg}"
        else:
            return f"❌ Error: {error_msg}", "", f"Error occurred: {error_msg}"

def load_config():
    """Load configuration from config.yml if it exists"""
    try:
        with open('config.yml', 'r') as f:
            data = yaml.full_load(f)
        api_keys_list = data.get("api_keys") or []
        api_keys_text = "\n".join([k for k in api_keys_list if k])
        return (
            data.get("api_key", DEFAULT_API_KEY),
            data.get("min_subscribers", 1000),
            data.get("max_subscribers", 100000),
            data.get("region_code", "US"),
            data.get("searching_keyword", ""),
            data.get("channel_views", 10000),
            data.get("videos_count", 1),
            data.get("start_date", "01/01/2020"),
            data.get("end_date", "01/12/2024"),
            api_keys_text
        )
    except:
        return DEFAULT_API_KEY, 1000, 100000, "US", "", 10000, 1, "01/01/2020", "01/12/2024", ""

# Load default values
default_api_key, default_min_subs, default_max_subs, default_region, default_keyword, default_views, default_videos, default_start, default_end, default_api_keys_text = load_config()

# Create Gradio interface
with gr.Blocks(title="YouTube Channel Scraper", theme=gr.themes.Soft()) as app:
    gr.Markdown("# 🎬 YouTube Channel Scraper")
    gr.Markdown("Find YouTube channels based on your criteria with subscriber range and creation date filtering.")
    key_offset_state = gr.State(0)
    
    with gr.Row():
        with gr.Column(scale=1):
            gr.Markdown("### 🔑 API Configuration")
            
            api_key_input = gr.Textbox(
                label="YouTube API Key",
                placeholder="Enter your YouTube Data API v3 key",
                value=default_api_key,
                type="password",
                info="Get your API key from Google Cloud Console"
            )
            api_keys_input = gr.Textbox(
                label="Additional API Keys (comma or newline separated)",
                placeholder="key1, key2, key3",
                lines=3,
                value=default_api_keys_text
            )
            
            gr.Markdown("### 🔍 Search Parameters")
            
            with gr.Tab("Single Search"):
                keyword = gr.Textbox(
                    label="Search Keyword",
                    placeholder="e.g., 'dj remix', 'cooking tutorial'",
                    value=default_keyword
                )
                keyword_expansion = gr.Checkbox(
                    label="Enhanced Keyword Expansion (YouTube Autocomplete)",
                    value=True,
                    info="Uses YouTube autocomplete API for better keyword suggestions"
                )
                max_expanded = gr.Slider(
                    label="Max expanded keywords",
                    minimum=1,
                    maximum=50,
                    value=10,
                    step=1
                )
                use_enhanced_autocomplete = gr.Checkbox(
                    label="Use Enhanced Autocomplete (Recommended)",
                    value=True,
                    info="Enables caching, rate limiting, and static fallback for better performance"
                )
            
            with gr.Tab("Bulk Keywords"):
                bulk_keywords = gr.Textbox(
                    label="Multiple Keywords (comma, semicolon, or newline separated)",
                    placeholder="dj remix, cooking tutorial, gaming highlights",
                    lines=3,
                    value=""
                )
            
            with gr.Tab("Trending Topics"):
                use_trending = gr.Checkbox(
                    label="Use Trending Topics",
                    value=False,
                    info="Automatically search using current trending topics"
                )
            
            region = gr.Dropdown(
                choices=["US", "IN", "GB", "CA", "AU", "DE", "FR", "JP", "BR", "MX"],
                label="Region Code",
                value=default_region
            )
            
            gr.Markdown("### 👥 Subscriber Range")
            with gr.Row():
                min_subs = gr.Number(
                    label="Min Subscribers",
                    value=default_min_subs,
                    minimum=0
                )
                max_subs = gr.Number(
                    label="Max Subscribers", 
                    value=default_max_subs,
                    minimum=1
                )
            
            gr.Markdown("### 📊 Channel Requirements")
            channel_views = gr.Number(
                label="Minimum Channel Views",
                value=default_views,
                minimum=0
            )
            
            videos_count = gr.Number(
                label="Minimum Videos Count",
                value=default_videos,
                minimum=1
            )
            
            gr.Markdown("### 📅 Creation Date Range")
            gr.Markdown("*Format: DD/MM/YYYY*")
            with gr.Row():
                start_date = gr.Textbox(
                    label="Start Date",
                    placeholder="01/01/2020",
                    value=default_start
                )
                end_date = gr.Textbox(
                    label="End Date", 
                    placeholder="01/12/2024",
                    value=default_end
                )
            
            max_pages = gr.Slider(
                label="Max Pages to Scrape",
                minimum=1,
                maximum=50,
                value=20,
                step=1
            )
            
            gr.Markdown("### 🔧 Advanced Features")
            
            with gr.Row():
                extract_social = gr.Checkbox(
                    label="Extract Social Media Links",
                    value=True,
                    info="Find Twitter, Instagram, Facebook, TikTok links"
                )
                extract_contact = gr.Checkbox(
                    label="Extract Contact Information", 
                    value=True,
                    info="Find emails, phone numbers, business inquiries"
                )
            
            gr.Markdown("### 🚀 Enhanced Autocomplete Settings")
            
            with gr.Row():
                cache_duration = gr.Slider(
                    label="Cache Duration (hours)",
                    minimum=1,
                    maximum=168,
                    value=24,
                    step=1,
                    info="How long to cache autocomplete results"
                )
                max_requests_per_minute = gr.Slider(
                    label="Max Requests per Minute",
                    minimum=1,
                    maximum=60,
                    value=10,
                    step=1,
                    info="Rate limiting for autocomplete API calls"
                )
            
            with gr.Row():
                use_alternative_sources = gr.Checkbox(
                    label="Use Alternative Sources",
                    value=True,
                    info="Try multiple autocomplete endpoints for better results"
                )
                fallback_to_static = gr.Checkbox(
                    label="Static Fallback",
                    value=True,
                    info="Use predefined suggestions when API quota is exhausted"
                )
            analyze_recent_videos = gr.Slider(
                label="Analyze N recent videos for frequency/category (0 = skip analysis, useful for dead channels)",
                minimum=0,
                maximum=50,
                value=0,
                step=1
            )
            
            save_per_keyword = gr.Checkbox(
                label="Save one file per keyword (bulk mode)",
                value=False,
                info="When using Bulk Keywords, writes a CSV per keyword with a timestamp"
            )
            
            with gr.Row():
                scrape_btn = gr.Button("🚀 Start Scraping", variant="primary", size="lg")
                next_key_btn = gr.Button("🔁 Next API Key", variant="secondary")
        
        with gr.Column(scale=2):
            gr.Markdown("### 📋 Results")
            
            summary_output = gr.Textbox(
                label="Summary",
                lines=10,
                max_lines=15
            )
            
            progress_output = gr.Textbox(
                label="Progress Log",
                lines=5,
                max_lines=10
            )
            
            results_output = gr.HTML(
                label="Channel Results",
                value="<p>Click 'Start Scraping' to begin...</p>"
            )
    
    # Connect the scraping function
    scrape_btn.click(
        fn=scrape_youtube_channels,
        inputs=[
            api_key_input, keyword, min_subs, max_subs, region, 
            channel_views, videos_count, start_date, end_date, max_pages,
            bulk_keywords, extract_social, extract_contact, use_trending, save_per_keyword,
            api_keys_input, keyword_expansion, max_expanded, analyze_recent_videos, key_offset_state,
            use_enhanced_autocomplete, cache_duration, max_requests_per_minute, 
            use_alternative_sources, fallback_to_static
        ],
        outputs=[summary_output, results_output, progress_output]
    )

    def increment_key_offset(current_offset):
        try:
            return (int(current_offset or 0) + 1)
        except:
            return 1

    next_key_btn.click(fn=increment_key_offset, inputs=[key_offset_state], outputs=[key_offset_state])
    
    gr.Markdown("---")
    gr.Markdown("### 💡 Tips:")
    gr.Markdown("""
    - **API Key**: Get your free YouTube Data API v3 key from [Google Cloud Console](https://console.cloud.google.com/)
    - **Enhanced Autocomplete**: Uses YouTube autocomplete API for better keyword suggestions with caching and rate limiting
    - **Single Search**: Use one specific keyword for targeted results with enhanced expansion
    - **Bulk Keywords**: Search multiple keywords separated by commas (e.g., "gaming, music, cooking")
    - **Trending Topics**: Automatically use current trending topics from your selected region
    - **Social Media**: Extracts Twitter, Instagram, Facebook, TikTok, and website links
    - **Contact Info**: Finds emails, phone numbers, and business inquiry contacts
    - **Quota Management**: Built-in caching and rate limiting to prevent API quota exhaustion
    - **Static Fallback**: Automatically uses predefined suggestions when API quota is exhausted
    - Adjust subscriber range based on your target audience
    - Date filtering helps find recently created channels
    - Results are automatically saved as CSV files with enhanced data
    - Large searches may take several minutes to complete
    - Use multiple API keys for higher quota limits
    """)

if __name__ == "__main__":
    app.launch(
        server_name="127.0.0.1",
        server_port=None,
        share=False,
        show_error=True,
        inbrowser=True
    )
