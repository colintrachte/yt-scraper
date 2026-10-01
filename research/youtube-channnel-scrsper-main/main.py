from googleapiclient.discovery import build
import pandas as pd
import yaml
from datetime import datetime
import re
import requests
import time
from autocomplete_apis import create_autocomplete_manager


with open('config.yml', 'r') as f:
    data = yaml.full_load(f)
min_subscribers = data.get("min_subscribers", 1000)
max_subscribers = data.get("max_subscribers", 1000000)
searching_keyword = data.get("searching_keyword")
region_code = data.get("region_code")
channel_views = data.get("channel_views")
videos_count = data.get("videos_count")
start_date = data.get("start_date")
end_date = data.get("end_date")
api_keys = [k for k in (data.get("api_keys") or []) if k]
keyword_expansion = data.get("keyword_expansion", True)
max_expanded_keywords = data.get("max_expanded_keywords", 10)
analyze_recent_videos = data.get("analyze_recent_videos", 0)

# YouTube autocomplete configuration
youtube_autocomplete_config = data.get("youtube_autocomplete", {})
youtube_autocomplete_enabled = youtube_autocomplete_config.get("enabled", True)
youtube_autocomplete_manager = create_autocomplete_manager(youtube_autocomplete_config) if youtube_autocomplete_enabled else None
def build_youtube_client(key):
    return build('youtube', 'v3', developerKey=key.strip())

def execute_with_key_rotation(request_builder_fn, keys):
    last_error = None
    for key in keys:
        try:
            yt = build_youtube_client(key)
            req = request_builder_fn(yt)
            return req.execute(), yt
        except Exception as e:
            err = str(e).lower()
            last_error = e
            if 'quotaexceeded' in err or 'forbidden' in err or 'apikeyinvalid' in err:
                continue
            raise
    if last_error is not None:
        raise last_error
    raise RuntimeError('No API keys configured')

if not api_keys:
    api_keys = ['xysabeigcshh']
youtube = build('youtube', 'v3', developerKey=api_keys[0])

def expand_keyword_base_forms(keyword):
    """Enhanced base form expansion with more variations"""
    variants = set()
    k = keyword.strip()
    if not k:
        return []
    variants.add(k)
    
    # Basic pluralization
    if not k.endswith('s'):
        variants.add(f"{k}s")
    if k.endswith('y'):
        variants.add(k[:-1] + 'ies')
    if k.endswith('es'):
        variants.add(k[:-2])
    
    # Enhanced modifiers
    modifiers = ['official', 'remix', 'mix', 'live', 'tutorial', 'best', 'top', '2024', 'trending', 
                'new', 'latest', 'popular', 'viral', 'amazing', 'incredible', 'epic', 'awesome']
    for m in modifiers:
        variants.add(f"{k} {m}")
        variants.add(f"{m} {k}")
    
    return list(variants)

def fetch_suggested_keywords(seed, region):
    """Legacy function for backward compatibility"""
    try:
        resp = requests.get(
            'https://suggestqueries.google.com/complete/search',
            params={'client': 'youtube', 'ds': 'yt', 'hl': region, 'q': seed},
            timeout=8
        )
        text = resp.text
        suggestions = re.findall(r'"(.*?)"', text)
        return [s for s in suggestions if s.lower() != seed.lower()][:20]
    except:
        return []

def generate_expanded_keywords_enhanced(seed, region, max_expanded):
    """Enhanced keyword expansion using YouTube autocomplete API"""
    if not youtube_autocomplete_manager:
        # Fallback to original method
        return generate_expanded_keywords_legacy(seed, region, max_expanded)
    
    print(f"Using YouTube autocomplete API for keyword expansion...")
    
    # Get base forms
    base_variants = expand_keyword_base_forms(seed)
    
    # Get comprehensive YouTube autocomplete suggestions
    youtube_suggestions = youtube_autocomplete_manager.get_comprehensive_youtube_suggestions(
        seed, region, max_results=max_expanded
    )
    
    # Combine base variants and YouTube suggestions
    all_keywords = set()
    all_keywords.update(base_variants)
    all_keywords.update(youtube_suggestions)
    
    # Remove duplicates and filter
    seen = []
    for k in all_keywords:
        if k and k.strip() and k.lower() not in [s.lower() for s in seen]:
            seen.append(k.strip())
        if len(seen) >= max_expanded:
            break
    
    print(f"Generated {len(seen)} YouTube-focused keywords")
    return seen if seen else [seed]

def generate_expanded_keywords_legacy(seed, region, max_expanded):
    """Legacy keyword expansion method"""
    base = expand_keyword_base_forms(seed)
    suggested = fetch_suggested_keywords(seed, region)
    expanded = list({*base, *suggested})
    seen = []
    for k in expanded:
        if k and k not in seen:
            seen.append(k)
        if len(seen) >= max_expanded:
            break
    return seen if seen else [seed]

def generate_expanded_keywords(seed, region, max_expanded):
    """Main keyword expansion function with YouTube autocomplete capabilities"""
    if youtube_autocomplete_enabled and youtube_autocomplete_manager:
        return generate_expanded_keywords_enhanced(seed, region, max_expanded)
    else:
        return generate_expanded_keywords_legacy(seed, region, max_expanded)

keywords_to_search = [searching_keyword]
if keyword_expansion:
    print(f"Starting keyword expansion for: '{searching_keyword}'")
    keywords_to_search = generate_expanded_keywords(searching_keyword, region_code, max_expanded_keywords)
    print(f"Final keywords to search: {keywords_to_search}")
    
    # Add delay between requests if configured
    delay = youtube_autocomplete_config.get('delay_between_requests', 0.5)
    if delay > 0:
        print(f"Adding {delay}s delay between API requests...")
        time.sleep(delay)

nextPageToken = ""
channels_info = []
for kw in keywords_to_search:
  nextPageToken = ""
  for i in range(20 // max(1, len(keywords_to_search)) + 1):
    try:
      def _build_req(yt):
        return yt.search().list(
          part="snippet",
          order="relevance",
          maxResults=50,
          q=str(kw),
          pageToken=nextPageToken,
          regionCode=region_code
        )
      response, youtube = execute_with_key_rotation(_build_req, api_keys)
      for item in response.get('items', []):
        channelId = item['snippet']['channelId']
        channel_title = item['snippet']['channelTitle']
        channel_desc = {"channelId": channelId, "channelTitle": channel_title, "ChannelLink":f"https://www.youtube.com/channel/{channelId}/"}
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
        
        channel_stats['items'][0]['statistics']
        stats_obj = channel_stats['items'][0]['statistics']
        channel_desc.update(stats_obj)
        # Engagement rate
        try:
          subs = int(stats_obj.get('subscriberCount', 0)) or 0
          views = int(stats_obj.get('viewCount', 0)) or 0
          channel_desc.update({"engagement_rate": round((views / subs), 3) if subs > 0 else None})
        except:
          pass
        # Upload frequency and dominant category (optional analysis)
        if analyze_recent_videos > 0:
          try:
            uploads_playlist = channel_stats['items'][0]['contentDetails']['relatedPlaylists']['uploads']
            def _recent_req(yt):
              return yt.playlistItems().list(part='snippet,contentDetails', playlistId=uploads_playlist, maxResults=min(50, max(5, analyze_recent_videos)))
            recent_resp, youtube = execute_with_key_rotation(_recent_req, api_keys)
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
            categories = []
            if video_ids:
              def _videos_req(yt):
                return yt.videos().list(part='snippet', id=','.join(video_ids[:50]))
              vids_resp, youtube = execute_with_key_rotation(_videos_req, api_keys)
              for v in vids_resp.get('items', []):
                cat_id = v['snippet'].get('categoryId')
                if cat_id:
                  categories.append(cat_id)
            if categories:
              def _cats_req(yt):
                return yt.videoCategories().list(part='snippet', regionCode=region_code)
              cats_resp, youtube = execute_with_key_rotation(_cats_req, api_keys)
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
        channels_info.append(channel_desc)
      nextPageToken = response.get("nextPageToken", "")
      # print(channels_info)
    except KeyError:
      break
    except Exception:
      break
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

if not channels_info:
    print("No channels found. Exiting.")
    # Still write an empty CSV to keep pipeline behavior predictable
    pd.DataFrame().to_csv(f"{searching_keyword}.csv", index=False)
    raise SystemExit(0)

channels_df = pd.DataFrame(channels_info)
for col, dtype in [('viewCount', 'int64'), ('subscriberCount', 'int64'), ('videoCount', 'int64')]:
    if col in channels_df.columns:
        channels_df[col] = pd.to_numeric(channels_df[col], errors='coerce').fillna(0).astype(dtype)

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

# Print summary
print(f"\\nScraping Summary:")
print(f"Total channels found: {len(channels_df)}")
print(f"Channels matching criteria: {len(results)}")
print(f"Subscriber range: {min_subscribers:,} - {max_subscribers:,}")
if start_date and end_date:
    print(f"Date range: {start_date} - {end_date}")
print(f"Results saved to: {searching_keyword}.csv\\n")

results.to_csv(f"{searching_keyword}.csv",index=False)