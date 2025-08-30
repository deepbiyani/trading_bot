from datetime import date

# Initial parameters
initial_principal = 11_60_000      # 01 Sep 2025
monthly_rate = 0.05                # 5% per month
monthly_investment = 0             # ₹0 per month
start_date = date(2025, 8, 29)      # Start date

# Get today's date
today = date.today()

# Calculate total days between start and today
total_days = (today - start_date).days

# Convert monthly rate to daily rate (approximation)
daily_rate = (1 + monthly_rate) ** (1/20) - 1

# Calculation
balance = initial_principal
balances = [balance]

for day in range(1, total_days + 1):
    balance *= (1 + daily_rate)   # Daily compounding
    # Optional: Add daily portion of monthly investment
    if monthly_investment > 0:
        balance += monthly_investment / 30
    balances.append(balance)

# Results
print(f"Start Date        : {start_date}")
print(f"Today             : {today}")
print(f"Days Elapsed      : {total_days}")
print(f"Initial Amount    : ₹{initial_principal:,.2f}")
print(f"Final Amount      : ₹{balances[-1]:,.2f}")
print(f"Target for today  : ₹{balances[-1] * daily_rate:,.2f}")
