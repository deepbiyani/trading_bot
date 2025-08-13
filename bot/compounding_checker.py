# Initial parameters
initial_principal = 11_33_000   # ₹11.25 lakh
monthly_rate = 0.03             # 2% per month
monthly_investment = 0          # ₹50k per month
months = 1                      # 5 years

# Variables
balance = initial_principal
balances = [balance]

print(f"Initial Amount \t  : ₹{initial_principal:,.2f} \n")

# Calculation loop
for month in range(1, months + 1):
    balance *= (1 + monthly_rate)      # Apply interest
    balance += monthly_investment      # Add monthly contribution
    balances.append(balance)

# Display results
for i, amount in enumerate(balances):
    print(f"Month {i} \t : ₹{amount:,.2f}")

# Final amount
print(f"\nFinal Amount after {months} months: ₹{balances[-1]:,.2f}")
print(f"\nTarget for this month : ₹{balances[-1] * monthly_rate:,.2f}")
print(f"\nTarget per trading day: ₹{balances[-1] * monthly_rate/20:,.2f}")
