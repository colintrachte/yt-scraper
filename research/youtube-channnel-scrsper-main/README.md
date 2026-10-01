# YouTube Channel Scraper

A powerful tool for discovering and analyzing YouTube channels based on specific keywords and criteria. Perfect for finding collaboration partners, conducting market research, and analyzing competitors.

## 🚀 Quick Start

1. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

2. **Configure your search** in `config.yml`:
   ```yaml
   searching_keyword: "cooking"
   region_code: "US"
   min_subscribers: 1000
   max_subscribers: 100000
   ```

3. **Run the scraper**:
   ```bash
   python main.py
   ```

4. **Get your results** in `{keyword}.csv`

## 📚 Documentation

### For Beginners
- **[Beginner Tutorial](BEGINNER_TUTORIAL.md)** - Step-by-step guide for first-time users
- **[Quick Reference](QUICK_REFERENCE.md)** - Essential settings and common use cases

### For Advanced Users
- **[Complete Guide](YOUTUBE_SCRAPER_GUIDE.md)** - Comprehensive documentation with advanced strategies
- **[Configuration Example](config_example.yml)** - Ready-to-use configuration templates

### Technical Documentation
- **[Autocomplete API Guide](AUTOCOMPLETE_GUIDE.md)** - YouTube autocomplete integration details

## 🎯 Key Features

### 🔍 Smart Keyword Discovery
- **YouTube Autocomplete Integration**: Uses multiple YouTube autocomplete APIs
- **Keyword Expansion**: Automatically generates relevant keyword variations
- **Long-tail Keywords**: Discovers specific, targeted search terms
- **Quota Management**: Intelligent caching and rate limiting

### 📊 Comprehensive Channel Analysis
- **Subscriber & View Metrics**: Detailed channel statistics
- **Engagement Analysis**: Views per subscriber ratios
- **Upload Frequency**: How often channels post content
- **Content Categories**: Dominant content types
- **Channel Age**: Creation date filtering

### ⚡ Performance Optimized
- **Intelligent Caching**: 24-hour cache reduces API calls
- **Rate Limiting**: Prevents quota exhaustion
- **Multiple API Keys**: Automatic key rotation
- **Static Fallbacks**: Continues working when APIs are exhausted

### 🌍 Global Coverage
- **Regional Targeting**: Search specific countries/regions
- **Date Filtering**: Find channels created in specific time periods
- **Flexible Criteria**: Customizable subscriber and view ranges

## 📈 Use Cases

### 🤝 Collaboration & Partnerships
Find channels for:
- Product reviews and sponsorships
- Cross-promotion opportunities
- Influencer partnerships
- Brand collaborations

### 📊 Market Research
Analyze:
- Competitor channels
- Market trends
- Content performance
- Audience engagement patterns

### 🔍 Channel Discovery
Discover:
- Emerging creators
- Niche communities
- Trending topics
- New content formats

## ⚙️ Configuration Options

### Basic Settings
```yaml
searching_keyword: "your keyword"     # Main search term
region_code: "US"                     # Target region
min_subscribers: 1000                 # Minimum subscribers
max_subscribers: 100000               # Maximum subscribers
```

### Enhanced Features
```yaml
keyword_expansion: true               # Enable keyword variations
max_expanded_keywords: 10             # Number of variations
analyze_recent_videos: 10             # Analyze recent content
youtube_autocomplete:
  enabled: true                       # Enhanced keyword discovery
  cache_duration_hours: 24            # Cache for 24 hours
```

### Performance Tuning
```yaml
# For faster results
max_expanded_keywords: 3
analyze_recent_videos: 0

# For comprehensive results
max_expanded_keywords: 15
analyze_recent_videos: 20
```

## 📊 Output Data

The scraper generates CSV files with detailed channel information:

| Column | Description |
|--------|-------------|
| `channelTitle` | Channel name |
| `ChannelLink` | Direct link to channel |
| `subscriberCount` | Number of subscribers |
| `viewCount` | Total channel views |
| `engagement_rate` | Views per subscriber ratio |
| `upload_frequency_per_day` | Upload frequency |
| `dominant_category_name` | Main content category |
| `country` | Channel's country |
| `publishedAt` | Channel creation date |

## 🔧 Troubleshooting

### Common Issues

**No Results Found**
- Increase `max_subscribers` range
- Remove date filters
- Try broader keywords
- Check `region_code`

**Quota Exhausted**
- Add more API keys
- Reduce `max_expanded_keywords`
- Enable caching
- Use `delay_between_requests: 1.0`

**Slow Performance**
- Reduce `analyze_recent_videos`
- Decrease `max_expanded_keywords`
- Use higher `min_subscribers`
- Enable caching

## 🛠️ Requirements

- Python 3.7+
- YouTube Data API v3 key (optional but recommended)
- Internet connection

## 📦 Installation

1. **Clone or download** this repository
2. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```
3. **Get YouTube API key** (optional):
   - Go to [Google Cloud Console](https://console.cloud.google.com/)
   - Create project and enable YouTube Data API v3
   - Create API key
4. **Configure** `config.yml` with your settings

## 🎯 Getting Started

### For Complete Beginners
1. Read the **[Beginner Tutorial](BEGINNER_TUTORIAL.md)**
2. Use the **[Quick Reference](QUICK_REFERENCE.md)** for common settings
3. Start with simple searches and gradually explore advanced features

### For Experienced Users
1. Check the **[Complete Guide](YOUTUBE_SCRAPER_GUIDE.md)** for advanced strategies
2. Use **[Configuration Examples](config_example.yml)** as starting points
3. Explore the **[Autocomplete API Guide](AUTOCOMPLETE_GUIDE.md)** for enhanced features

## 📞 Support

### Documentation
- **[Beginner Tutorial](BEGINNER_TUTORIAL.md)** - Step-by-step guide
- **[Quick Reference](QUICK_REFERENCE.md)** - Essential settings
- **[Complete Guide](YOUTUBE_SCRAPER_GUIDE.md)** - Comprehensive documentation

### Common Solutions
- Check your `config.yml` configuration
- Verify your API keys
- Test with simple parameters first
- Review error messages in the terminal

## 🚀 Advanced Features

### YouTube Autocomplete Integration
- Multiple autocomplete endpoints
- Intelligent caching system
- Quota management
- Static fallback suggestions

### Smart Keyword Expansion
- Base form variations
- Long-tail keyword generation
- YouTube-specific modifiers
- Content type variations

### Performance Optimization
- Request caching
- Rate limiting
- Multiple API key rotation
- Intelligent request management

## 📈 Best Practices

1. **Start Small**: Begin with simple searches and basic parameters
2. **Test Thoroughly**: Verify results before scaling up
3. **Use Multiple Searches**: Run different keyword variations
4. **Monitor Performance**: Adjust settings based on results
5. **Keep Data Fresh**: Re-run searches periodically

## 🎉 Success Stories

This tool has helped users:
- Find 500+ collaboration partners in their niche
- Discover emerging channels before they became popular
- Analyze competitor strategies and content performance
- Build comprehensive databases of relevant channels
- Identify market opportunities and content gaps

---

## 📄 License

This project is open source and available under the MIT License.

## 🤝 Contributing

Contributions are welcome! Please feel free to submit issues, feature requests, or pull requests.

---

**Ready to discover amazing YouTube channels? Start with the [Beginner Tutorial](BEGINNER_TUTORIAL.md) and happy scraping! 🚀**
