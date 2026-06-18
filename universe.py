"""Stock universe definitions."""

SP100 = [
    "AAPL","MSFT","NVDA","AMZN","META","GOOGL","TSLA","LLY","AVGO","JPM",
    "UNH","XOM","V","MA","COST","JNJ","PG","HD","ABBV","MRK",
    "CVX","CRM","BAC","KO","PEP","ORCL","TMO","ACN","WMT","MCD",
    "CSCO","ABT","IBM","GE","CAT","TXN","QCOM","GS","ISRG","SPGI",
    "MS","AMAT","AMGN","INTU","AXP","BKNG","BLK","MDT","SYK","HON",
    "SCHW","C","DE","ADI","VRTX","REGN","LRCX","NOW","GILD","ZTS",
    "CME","EOG","NSC","SHW","ITW","MO","DUK","SO","TT","MMC",
    "MCO","COP","HCA","EMR","KLAC","CDNS","SNPS","PANW","FTNT","MELI",
    "NFLX","ROP","APD","WFC","PNC","ADP","CTAS","NKE","LOW","TGT",
    "SBUX","DHR","IDXX","TMO","MTD","EW","DXCM","MRNA","BIIB","ILMN",
]

NASDAQ100 = [
    "AAPL","MSFT","NVDA","AMZN","META","GOOGL","TSLA","AVGO","COST","CSCO",
    "TMUS","ADBE","PEP","NFLX","AMD","INTU","QCOM","AMAT","HON","TXN",
    "ISRG","BKNG","PANW","VRTX","LRCX","GILD","REGN","MU","ADI","KLAC",
    "SNPS","CDNS","FTNT","MELI","PYPL","DXCM","ODFL","CRWD","IDXX","MNST",
    "ORLY","ROST","FAST","CPRT","PAYX","PCAR","ON","ABNB","CEG","TTD",
    "ZS","ANSS","SMCI","TTWO","TEAM","ALGN","BIIB","ILMN","MRNA","WBD",
    "INTC","KDP","DLTR","VRSK","BKR","FANG","EXC","AEP","SPLK","DDOG",
]

SP500_SAMPLE = [
    # Technology
    "AAPL","MSFT","NVDA","AVGO","ORCL","CRM","CSCO","IBM","TXN","QCOM",
    "AMAT","LRCX","KLAC","SNPS","CDNS","ADI","MU","INTC","FTNT","PANW",
    "CDNS","NOW","INTU","ADBE","AMD","ON","MRVL","ANSS","FSLR","ENPH",
    # Healthcare
    "UNH","JNJ","LLY","ABBV","MRK","TMO","ABT","MDT","SYK","ISRG",
    "VRTX","REGN","GILD","AMGN","BIIB","IDXX","DHR","EW","DXCM","ZTS",
    "HCA","MRNA","ILMN","BAX","BDX","HOLX","IQV","MTD","PODD","TECH",
    # Financial Services
    "JPM","BAC","WFC","GS","MS","C","SCHW","BLK","SPGI","MCO",
    "AXP","CME","ICE","COF","USB","PNC","TFC","BK","STT","FITB",
    # Consumer Cyclical
    "AMZN","TSLA","HD","MCD","NKE","LOW","SBUX","BKNG","MAR","HLT",
    "TGT","COST","TJX","GM","F","ORLY","AZO","DLTR","ROST","YUM",
    # Energy
    "XOM","CVX","COP","EOG","SLB","PXD","MPC","VLO","PSX","OXY",
    "DVN","FANG","HES","APA","CTRA","MRO","EQT","HAL","BKR","NOV",
    # Industrials
    "HON","GE","CAT","DE","ITW","EMR","NSC","UNP","CSX","FDX",
    "UPS","RTX","LMT","NOC","GD","BA","MMM","PH","ROK","TT",
    "CTAS","ROP","ODFL","FAST","PCAR","CPRT","IR","ETN","AME","XYL",
    # Basic Materials
    "LIN","APD","SHW","DD","PPG","ECL","NEM","FCX","NUE","STLD",
    "CF","MOS","ALB","FMC","IFF","CE","EMN","HUN","OLN","AXTA",
    # Communication Services
    "GOOGL","META","NFLX","DIS","TMUS","CMCSA","T","VZ","CHTR","TTWO",
    # Consumer Defensive
    "PG","KO","PEP","WMT","COST","MO","PM","MDLZ","CL","GIS",
    "KMB","HSY","SJM","CPB","CAG","MKC","KHC","TSN","HRL","POST",
    # Real Estate
    "PLD","AMT","CCI","EQIX","PSA","O","WELL","DLR","EXR","AVB",
    # Utilities
    "NEE","DUK","SO","D","AEP","XEL","EXC","WEC","ES","AWK",
]

UNIVERSES = {
    "sp100":    SP100,
    "sp500":    SP500_SAMPLE,
    "nasdaq100": NASDAQ100,
}
