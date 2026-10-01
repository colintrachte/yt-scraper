# YouTube Channel Scraper - Complete Usage Guide

## 🎯 Overview

This YouTube scraper helps you discover and analyze YouTube channels based on specific keywords and criteria. It's designed to find channels that match your exact requirements for subscriber count, view count, region, and more.

## 🚀 Quick Start

### 1. Basic Setup
1. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

2. **Configure your search** in `config.yml`:
   ```yaml
   searching_keyword: "your keyword here"
   region_code: "US"  # or your target region
   ```

3. **Run the scraper**:
   ```bash
   python main.py
   ```

### 2. Get Your Results
- Results are saved as `{keyword}.csv` in the same directory
- Open the CSV file in Excel, Google Sheets, or any spreadsheet application

## 📋 Configuration Guide

### Essential Settings

#### Basic Search Configuration
```yaml
# Your main search keyword
searching_keyword: "cooking"

# Target region (ISO country code)
region_code: "US"  # Options: US, IN, UK, CA, AU, etc.

# Subscriber range filter
min_subscribers: 1000      # Minimum subscribers
max_subscribers: 100000    # Maximum subscribers

# Channel requirements
channel_views: 20000       # Minimum total channel views
videos_count: 1            # Minimum number of videos
```

#### Date Range Filtering
```yaml
# Optional: Filter channels by creation date
start_date: "01/01/2020"   # DD/MM/YYYY format
end_date: "01/09/2025"     # DD/MM/YYYY format
```

#### Keyword Expansion (Recommended)
```yaml
# Enable keyword expansion for better results
keyword_expansion: true
max_expanded_keywords: 10  # Number of keyword variations to search

# YouTube Autocomplete API (Enhanced keyword discovery)
youtube_autocomplete:
  enabled: true
  max_suggestions: 20
  include_long_tail: true
```

### Advanced Configuration

#### API Key Management
```yaml
# Add multiple YouTube API keys for better quota management
api_keys:
  - "your_first_api_key"
  - "your_second_api_key"
  - "your_third_api_key"
```

#### Performance Settings
```yaml
# Analyze recent videos for better insights
analyze_recent_videos: 10  # Number of recent videos to analyze

# YouTube Autocomplete quota management
youtube_autocomplete:
  cache_duration_hours: 24
  max_requests_per_minute: 10
  delay_between_requests: 0.5
```

## 🎯 Maximizing Your Results

### 1. Choose the Right Keywords

#### Effective Keywords
- **Specific niches**: "vegan cooking", "gaming setup", "fitness motivation"
- **Content types**: "tutorial", "review", "reaction", "cover"
- **Languages**: Use keywords in your target language
- **Trending topics**: Include current year or trending terms

#### Keyword Expansion Strategy
```yaml
# Enable keyword expansion for comprehensive results
keyword_expansion: true
max_expanded_keywords: 15  # Increase for more variations

# The system will automatically generate:
# - "cooking" → "cooking tutorial", "cooking tips", "cooking class"
# - "music" → "music video", "music cover", "music reaction"
```

### 2. Optimize Your Filters

#### Subscriber Range Strategy
```yaml
# For finding emerging channels
min_subscribers: 1000
max_subscribers: 50000

# For established channels
min_subscribers: 100000
max_subscribers: 1000000

# For micro-influencers
min_subscribers: 10000
max_subscribers: 100000
```

#### Regional Targeting
```yaml
# Target specific regions for better relevance
region_code: "US"    # United States
region_code: "IN"    # India
region_code: "UK"    # United Kingdom
region_code: "BR"    # Brazil
region_code: "DE"    # Germany
```

### 3. Use Date Filtering Effectively

#### Find New Channels
```yaml
# Channels created in the last 2 years
start_date: "01/01/2023"
end_date: "01/01/2025"
```

#### Find Established Channels
```yaml
# Channels created 3+ years ago
start_date: "01/01/2020"
end_date: "01/01/2022"
```

### 4. Leverage Advanced Features

#### Recent Video Analysis
```yaml
# Analyze recent videos for engagement insights
analyze_recent_videos: 20  # Analyze last 20 videos
```

This provides:
- Upload frequency per day
- Dominant content category
- Engagement patterns

#### Multiple API Keys
```yaml
# Use multiple API keys to avoid quota limits
api_keys:
  - "key1"
  - "key2"
  - "key3"
  - "key4"
```

## 📊 Understanding Your Results

### CSV Output Columns

| Column | Description |
|--------|-------------|
| `channelId` | Unique YouTube channel ID |
| `channelTitle` | Channel name |
| `ChannelLink` | Direct link to channel |
| `country` | Channel's country |
| `publishedAt` | Channel creation date |
| `subscriberCount` | Number of subscribers |
| `viewCount` | Total channel views |
| `videoCount` | Number of videos |
| `engagement_rate` | Views per subscriber ratio |
| `upload_frequency_per_day` | How often they upload |
| `dominant_category_name` | Main content category |

### Key Metrics to Analyze

#### 1. Engagement Rate
- **High engagement** (>10): Very active audience
- **Medium engagement** (5-10): Good audience interaction
- **Low engagement** (<5): May have inactive subscribers

#### 2. Upload Frequency
- **Daily uploaders** (>0.5/day): High content producers
- **Regular uploaders** (0.1-0.5/day): Consistent creators
- **Occasional uploaders** (<0.1/day): Sporadic content

#### 3. Channel Age vs Growth
- **New channels** with high engagement: Potential for growth
- **Established channels** with steady growth: Reliable partners

## 🎯 Use Cases & Strategies

### 1. Finding Collaboration Partners

#### For Brand Partnerships
```yaml
searching_keyword: "product review"
min_subscribers: 10000
max_subscribers: 100000
region_code: "US"
analyze_recent_videos: 15
```

#### For Cross-Promotion
```yaml
searching_keyword: "your niche"
min_subscribers: 5000
max_subscribers: 50000
region_code: "US"
```

### 2. Market Research

#### Competitor Analysis
```yaml
searching_keyword: "your main keyword"
min_subscribers: 100000
max_subscribers: 1000000
region_code: "US"
start_date: "01/01/2020"
end_date: "01/01/2025"
```

#### Emerging Trends
```yaml
searching_keyword: "trending topic"
min_subscribers: 1000
max_subscribers: 50000
start_date: "01/01/2024"  # Recent channels only
```

### 3. Content Inspiration

#### Find Successful Content Types
```yaml
searching_keyword: "tutorial"
min_subscribers: 50000
max_subscribers: 500000
analyze_recent_videos: 20
```

## ⚡ Performance Optimization

### 1. Speed Up Your Scraping

#### Reduce Analysis Depth
```yaml
# For faster results, reduce analysis
analyze_recent_videos: 5   # Instead of 20
max_expanded_keywords: 5   # Instead of 15
```

#### Use Caching
```yaml
youtube_autocomplete:
  cache_duration_hours: 48  # Cache for 2 days
```

### 2. Handle Large Datasets

#### Batch Processing
```yaml
# For large searches, use smaller batches
max_expanded_keywords: 5
min_subscribers: 10000  # Higher minimum for fewer results
```

#### Multiple Runs
Run multiple searches with different parameters:
1. First run: Broad search with basic filters
2. Second run: Refined search with specific criteria
3. Third run: Niche search with exact requirements

## 🔧 Troubleshooting

### Common Issues & Solutions

#### 1. No Results Found
**Problem**: CSV file is empty or has very few results
**Solutions**:
- Increase `max_subscribers` range
- Remove or adjust date filters
- Try broader keywords
- Check if `region_code` is too restrictive

#### 2. Quota Exhausted
**Problem**: API quota limits reached
**Solutions**:
- Add more API keys to `api_keys` list
- Reduce `max_expanded_keywords`
- Enable caching: `cache_duration_hours: 24`
- Use `delay_between_requests: 1.0`

#### 3. Slow Performance
**Problem**: Scraping takes too long
**Solutions**:
- Reduce `analyze_recent_videos` to 5 or 0
- Decrease `max_expanded_keywords`
- Use higher `min_subscribers` to reduce results
- Enable caching

#### 4. Poor Keyword Results
**Problem**: Keywords not generating good suggestions
**Solutions**:
- Enable `youtube_autocomplete.enabled: true`
- Increase `max_suggestions: 30`
- Use more specific keywords
- Try different `region_code`

## 📈 Advanced Tips

### 1. Keyword Research Strategy

#### Use Trending Keywords
- Include current year: "cooking 2024"
- Add trending terms: "viral", "trending", "popular"
- Use seasonal keywords: "summer", "winter", "holiday"

#### Long-tail Keywords
```yaml
# Enable long-tail keyword generation
youtube_autocomplete:
  include_long_tail: true
  max_long_tail_variations: 20
```

### 2. Data Analysis Tips

#### Excel/Google Sheets Formulas
```excel
# Calculate engagement rate
=viewCount/subscriberCount

# Find channels with high upload frequency
=upload_frequency_per_day>0.5

# Filter by dominant category
=FILTER(data, dominant_category_name="Entertainment")
```

#### Sorting Strategies
1. **By Engagement Rate**: Find most active audiences
2. **By Upload Frequency**: Find consistent creators
3. **By Subscriber Count**: Find right-sized channels
4. **By Channel Age**: Find new vs established channels

### 3. Automation Ideas

#### Batch Processing Script
Create multiple config files for different searches:
- `config_cooking.yml`
- `config_gaming.yml`
- `config_fitness.yml`

#### Scheduled Runs
Set up automated runs for:
- Weekly competitor analysis
- Monthly trend discovery
- Quarterly market research

## 🎯 Best Practices

### 1. Start Small, Scale Up
1. **Test with small parameters** first
2. **Verify results** make sense
3. **Gradually increase** scope
4. **Monitor performance** and adjust

### 2. Use Multiple Search Strategies
1. **Broad search**: General keywords, wide filters
2. **Niche search**: Specific keywords, tight filters
3. **Competitor search**: Similar channel analysis
4. **Trend search**: Recent and trending topics

### 3. Validate Your Results
1. **Spot check** a few channels manually
2. **Verify metrics** make sense
3. **Check engagement** rates
4. **Confirm content** relevance

### 4. Keep Your Data Fresh
1. **Re-run searches** periodically
2. **Update keywords** based on trends
3. **Adjust filters** based on results
4. **Monitor new channels** regularly

## 📞 Support & Resources

### Getting Help
1. **Check logs** for error messages
2. **Verify configuration** in `config.yml`
3. **Test with simple parameters** first
4. **Review this guide** for common solutions

### Useful Resources
- **YouTube API Documentation**: For understanding quotas and limits
- **Country Codes**: For `region_code` values
- **YouTube Analytics**: For understanding metrics
- **CSV Analysis Tools**: Excel, Google Sheets, Python pandas

---

## 🎉 Conclusion

This YouTube scraper is a powerful tool for discovering and analyzing YouTube channels. By following this guide and experimenting with different configurations, you can:

- **Find the perfect collaboration partners**
- **Discover emerging trends and channels**
- **Analyze your competition**
- **Identify market opportunities**
- **Build comprehensive channel databases**

Remember: Start simple, test thoroughly, and scale up gradually. The key to success is understanding your specific needs and configuring the scraper accordingly.

**Happy scraping! 🚀**
