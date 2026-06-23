# FinIntelX: An Open-Source AI Agent Platform for Financial Analysis using Large Language Models

FinIntelX is a premium AI Agent platform tailored for financial applications. It unifies multiple AI technologies—including LLMs, reinforcement learning, and quantitative analytics—to power investment research automation, algorithmic trading strategies, and risk assessment, delivering a full-stack intelligent solution for the financial industry.

## 🎬 FinIntelX Pro — Your Personal AI-Powered Equity Research Assistant

A locally-deployed AI assistant that fetches financial data, runs multi-agent LLM analysis, and generates professional equity research reports.

**1. Configure API Keys**
```bash
cp finrobot_equity/core/config/config.ini.example finrobot_equity/core/config/config.ini
```
Edit `config.ini` with your keys:
```ini
[API_KEYS]
fmp_api_key = YOUR_FMP_API_KEY          # https://financialmodelingprep.com/developer
openai_api_key = YOUR_OPENAI_API_KEY    # https://platform.openai.com/account/api-keys
adanos_api_key = YOUR_ADANOS_API_KEY    # Optional: enables Retail Sentiment Insights
```

**2. One-Command Deploy (Web Interface)**
```bash
chmod +x deploy.sh
./deploy.sh start

# if deploy.sh not working then
python3 -m venv venv                                                                                                                                           
source venv/bin/activate
pip install -r requirements-equity.txt                                                                                                                         
python run_web_app.py  
```
Access at `http://127.0.0.1:8001`

| Command | Description |
|:---|:---|
| `./deploy.sh start` | Start the web app (auto-installs dependencies) |
| `./deploy.sh stop` | Stop the application |
| `./deploy.sh restart` | Restart the application |
| `./deploy.sh status` | Check running status |

**3. Or Run via Command Line**
```bash
# Step 1: Financial analysis
python finrobot_equity/core/src/generate_financial_analysis.py \
    --company-ticker NVDA \
    --company-name "NVIDIA Corporation" \
    --config-file finrobot_equity/core/config/config.ini \
    --peer-tickers AMD INTC \
    --generate-text-sections

# Step 2: Generate report
python finrobot_equity/core/src/create_equity_report.py \
    --company-ticker NVDA \
    --company-name "NVIDIA Corporation" \
    --analysis-csv output/NVDA/analysis/financial_metrics_and_forecasts.csv \
    --ratios-csv output/NVDA/analysis/ratios_raw_data.csv \
    --config-file finrobot_equity/core/config/config.ini
```

**Pipeline**:
1. **Fetch Financial Data**: income statements, balance sheets, cash flows via FMP API
2. **Process & Forecast**: 3-year financial projections, DCF valuation, peer comparison
3. **AI Agent Analysis**: 8 specialized agents generate investment thesis, risk assessment, valuation overview, etc.
4. **Report Generation**: professional multi-page HTML/PDF with 15+ chart types

For full documentation, see [finrobot_equity/README.md](finrobot_equity/README.md).

## What is FinIntelX Pro?

FinIntelX Pro is an AI-powered equity research platform that automates professional stock analysis using Large Language Models (LLMs) and AI Agents.

**Key Features:**
- **Automated Report Generation** – Generate professional equity research reports instantly
- **Financial Analysis** – Deep dive into income statements, balance sheets, and cash flows
- **Valuation Analysis** – P/E ratio, EV/EBITDA multiples, and peer comparison
- **Risk Assessment** – Comprehensive investment risk evaluation

## File Structure

The main folder **finrobot** has three subfolders **agents, data_source, functional**. 

```
FinIntelX
├── finrobot (main folder)
│   ├── agents
│   	├── agent_library.py
│   	└── workflow.py
│   ├── data_source
│   	├── finnhub_utils.py
│   	├── finnlp_utils.py
│   	├── fmp_utils.py
│   	├── sec_utils.py
│   	└── yfinance_utils.py
│   ├── functional
│   	├── analyzer.py
│   	├── charting.py
│   	├── coding.py
│   	├── quantitative.py
│   	├── reportlab.py
│   	└── text.py
│   ├── toolkits.py
│   └── utils.py
│
├── configs
├── experiments
├── tutorials_beginner (hands-on tutorial)
│   ├── agent_fingpt_forecaster.ipynb
│   └── agent_annual_report.ipynb 
├── tutorials_advanced (advanced tutorials for potential developers)
│   ├── agent_trade_strategist.ipynb
│   ├── agent_fingpt_forecaster.ipynb
│   ├── agent_annual_report.ipynb 
│   ├── lmm_agent_mplfinance.ipynb
│   └── lmm_agent_opt_smacross.ipynb
├── setup.py
├── OAI_CONFIG_LIST_sample
├── config_api_keys_sample
├── requirements.txt
└── README.md
```

## Installation:

**1. Create a new virtual environment**
```shell
python3 -m venv venv
source venv/bin/activate
```
**2. Download the repo and navigate to it**
```shell
cd FinIntelX
```
**3. Install dependencies**
```bash
pip install -e .
```
**4. Setup configuration**
- Rename `OAI_CONFIG_LIST_sample` to `OAI_CONFIG_LIST` and add your OpenAI API key
- Rename `config_api_keys_sample` to `config_api_keys` and add your FMP/SEC API keys

**Disclaimer**: The codes and documents provided herein are released under the Apache-2.0 license. They should not be construed as financial counsel or recommendations for live trading. It is imperative to exercise caution and consult with qualified financial professionals prior to any trading or investment actions.
