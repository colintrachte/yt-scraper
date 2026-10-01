# YouTube Scraper - Beginner Tutorial

## 🎯 What This Tool Does

This YouTube scraper helps you find YouTube channels that match your specific criteria. For example:
- Find cooking channels with 10,000-100,000 subscribers in the US
- Discover gaming channels created in the last 2 years
- Analyze fitness channels with high engagement rates

## 🚀 Step-by-Step Tutorial

### Step 1: Setup (5 minutes)

1. **Make sure you have Python installed**
   - Download from [python.org](https://python.org) if needed

2. **Install the required packages**
   ```bash
   pip install -r requirements.txt
   ```

3. **Get a YouTube API key** (optional but recommended)
   - Go to [Google Cloud Console](https://console.cloud.google.com/)
   - Create a new project
   - Enable YouTube Data API v3
   - Create credentials (API key)
   - Copy the API key

### Step 2: Configure Your Search (2 minutes)

1. **Open `config.yml` in a text editor**

2. **Set your basic search parameters**:
   ```yaml
   searching_keyword: "cooking"
   region_code: "US"
   min_subscribers: 1000
   max_subscribers: 100000
   ```

3. **Add your API key** (if you have one):
   ```yaml
   api_keys:
     - "your_api_key_here"
   ```

### Step 3: Run Your First Search (1 minute)

1. **Open terminal/command prompt**
2. **Navigate to the scraper folder**
3. **Run the scraper**:
   ```bash
   python main.py
   ```

4. **Wait for completion** (usually 1-5 minutes)

### Step 4: View Your Results (2 minutes)

1. **Find the CSV file** named `{your_keyword}.csv`
2. **Open it in Excel, Google Sheets, or any spreadsheet app**
3. **Explore the data**:
   - Channel names and links
   - Subscriber counts
   - View counts
   - Engagement rates
   - Upload frequency

## 📊 Understanding Your Results

### What Each Column Means

| Column | What It Shows |
|--------|---------------|
| `channelTitle` | Channel name |
| `ChannelLink` | Direct link to channel |
| `subscriberCount` | Number of subscribers |
| `viewCount` | Total views on all videos |
| `engagement_rate` | Views per subscriber (higher = better) |
| `upload_frequency_per_day` | How often they upload videos |
| `dominant_category_name` | Main type of content |

### What Makes a Good Channel?

- **High engagement rate** (>5): Audience is active and engaged
- **Regular uploads** (>0.1/day): Consistent content creation
- **Relevant content**: Matches your keyword and interests
- **Right size**: Not too big (hard to reach) or too small (limited reach)

## 🎯 Common Use Cases

### 1. Find Collaboration Partners

**Goal**: Find channels to collaborate with or sponsor

**Configuration**:
```yaml
searching_keyword: "product review"
min_subscribers: 10000
max_subscribers: 100000
region_code: "US"
```

**What to look for**:
- Channels that review products similar to yours
- Good engagement rates (>5)
- Regular upload schedule
- Professional content quality

### 2. Market Research

**Goal**: Understand your competition and market

**Configuration**:
```yaml
searching_keyword: "your niche"
min_subscribers: 50000
max_subscribers: 500000
region_code: "US"
```

**What to analyze**:
- What content performs well
- How often successful channels upload
- What engagement rates are typical
- Content gaps you could fill

### 3. Find New Channels

**Goal**: Discover emerging channels before they get big

**Configuration**:
```yaml
searching_keyword: "trending topic"
min_subscribers: 1000
max_subscribers: 50000
start_date: "01/01/2024"
```

**What to look for**:
- High engagement rates despite small size
- Consistent upload schedule
- Quality content
- Growing subscriber count

## 🔧 Troubleshooting Common Issues

### Problem: No Results Found

**Symptoms**: CSV file is empty or has very few results

**Solutions**:
1. **Increase subscriber range**:
   ```yaml
   min_subscribers: 1000
   max_subscribers: 500000  # Increase this
   ```

2. **Remove date filters**:
   ```yaml
   # Comment out or remove these lines
   # start_date: "01/01/2020"
   # end_date: "01/01/2025"
   ```

3. **Try broader keywords**:
   ```yaml
   searching_keyword: "cooking"  # Instead of "vegan cooking recipes"
   ```

4. **Check region code**:
   ```yaml
   region_code: "US"  # Make sure this is correct
   ```

### Problem: Scraping Takes Too Long

**Symptoms**: Process runs for more than 10 minutes

**Solutions**:
1. **Reduce analysis depth**:
   ```yaml
   analyze_recent_videos: 5  # Instead of 20
   max_expanded_keywords: 5  # Instead of 15
   ```

2. **Use higher minimum subscribers**:
   ```yaml
   min_subscribers: 10000  # Fewer channels to analyze
   ```

3. **Disable recent video analysis**:
   ```yaml
   analyze_recent_videos: 0  # Skip this analysis
   ```

### Problem: API Quota Exhausted

**Symptoms**: Error messages about quota limits

**Solutions**:
1. **Add more API keys**:
   ```yaml
   api_keys:
     - "key1"
     - "key2"
     - "key3"
   ```

2. **Reduce keyword expansion**:
   ```yaml
   max_expanded_keywords: 3  # Fewer variations
   ```

3. **Enable caching**:
   ```yaml
   youtube_autocomplete:
     cache_duration_hours: 24
   ```

## 📈 Pro Tips for Beginners

### 1. Start Simple
- Begin with basic searches
- Use simple keywords
- Don't enable all features at once
- Test with small subscriber ranges first

### 2. Learn from Results
- Look at successful channels
- Note what makes them successful
- Identify patterns in engagement
- Understand your target audience

### 3. Iterate and Improve
- Run multiple searches with different parameters
- Try different keywords
- Adjust subscriber ranges
- Test different regions

### 4. Validate Your Findings
- Visit channels manually
- Check if content matches your needs
- Verify engagement rates
- Confirm channel activity

## 🎯 Next Steps

Once you're comfortable with basic usage:

1. **Read the full guide**: `YOUTUBE_SCRAPER_GUIDE.md`
2. **Use the quick reference**: `QUICK_REFERENCE.md`
3. **Try advanced features**:
   - Multiple API keys
   - Date filtering
   - Advanced keyword expansion
   - Recent video analysis

4. **Experiment with different use cases**:
   - Competitor analysis
   - Trend discovery
   - Market research
   - Partnership opportunities

## 📞 Getting Help

### If Something Goes Wrong

1. **Check the error message** in the terminal
2. **Verify your configuration** in `config.yml`
3. **Try simpler settings** first
4. **Check your internet connection**
5. **Verify your API key** (if using one)

### Common Error Messages

- **"No API keys configured"**: Add API keys to config.yml
- **"Quota exceeded"**: Add more API keys or reduce search scope
- **"No channels found"**: Increase subscriber range or try broader keywords
- **"Connection error"**: Check internet connection

### Resources

- **Full Guide**: `YOUTUBE_SCRAPER_GUIDE.md`
- **Quick Reference**: `QUICK_REFERENCE.md`
- **Example Config**: `config_example.yml`
- **YouTube API Docs**: [developers.google.com/youtube](https://developers.google.com/youtube)

---

## 🎉 Congratulations!

You now know how to use the YouTube scraper! Start with simple searches and gradually explore more advanced features. Remember: the key to success is understanding your specific needs and configuring the scraper accordingly.

**Happy scraping! 🚀**
