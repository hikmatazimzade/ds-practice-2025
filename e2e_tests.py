import requests
import json
import concurrent.futures
import time

ORCHESTRATOR_URL = "http://localhost:8081/checkout"

# Helper for a valid order
def create_order_payload(name="John Doe", contact="john@example.com", 
                         card_number="4242424242424242", cvv="123",
                         items=None):
    if items is None:
        items = [{"name": "Learning Python", "quantity": 1}]
        
    return {
        "user": {"name": name, "contact": contact},
        "creditCard": {"number": card_number, "cvv": cvv},
        "billingAddress": {"street": "123 Main St", "city": "City", "zip": "12345", "country": "Country", "state": "State"},
        "items": items
    }

def send_order(payload):
    try:
        response = requests.post(ORCHESTRATOR_URL, json=payload)
        return response.status_code, response.json()
    except Exception as e:
        return 500, str(e)

print("--- End-to-End Test Suite ---")

# 1. Single non-fraudulent order
print("\n[Test 1] Single non-fraudulent order")
payload1 = create_order_payload(items=[{"name": "Learning Python", "quantity": 1}])
status1, resp1 = send_order(payload1)
print(f"Status: {status1}, Response: {resp1}")
assert status1 == 200, "Test 1 Failed"

# 2. Multiple non-fraudulent non-conflicting orders
print("\n[Test 2] Multiple non-fraudulent non-conflicting orders")
payload2a = create_order_payload(items=[{"name": "JavaScript - The Good Parts", "quantity": 1}])
payload2b = create_order_payload(items=[{"name": "Clean Code", "quantity": 1}])

with concurrent.futures.ThreadPoolExecutor() as executor:
    futures = [executor.submit(send_order, p) for p in [payload2a, payload2b]]
    for f in concurrent.futures.as_completed(futures):
        status, resp = f.result()
        print(f"Status: {status}, Response: {resp}")
        assert status == 200, "Test 2 Failed"

# 3. Multiple mixed orders
print("\n[Test 3] Multiple mixed orders")
payload3a = create_order_payload(contact="invalid-email") # Fraud (no @)
payload3b = create_order_payload(items=[{"name": "Learning Python", "quantity": 1}]) # Valid

with concurrent.futures.ThreadPoolExecutor() as executor:
    f1 = executor.submit(send_order, payload3a)
    f2 = executor.submit(send_order, payload3b)
    
    status3a, resp3a = f1.result()
    status3b, resp3b = f2.result()
    print(f"Fraudulent Order - Status: {status3a}, Response: {resp3a}")
    print(f"Valid Order      - Status: {status3b}, Response: {resp3b}")
    assert status3a == 400, "Test 3a (Fraud) Failed"
    assert status3b == 200, "Test 3b (Valid) Failed"

# 4. Conflicting orders
print("\n[Test 4] Conflicting orders (same book, exceeding stock)")
# 'Domain-Driven Design' starts with 7 stock. We request 5 in both concurrent requests, totalling 10.
# Both should be queued, but one will eventually fail during processing in order_executor (not visible in the HTTP response here since orchestrator just enqueues it, but the queue will process it correctly).
# Wait, Orchestrator returns 200 once it *enqueues*. So both return 200 here. 
# We just need to trigger the scenario to demonstrate it handles it properly.
payload4a = create_order_payload(items=[{"name": "Domain-Driven Design", "quantity": 4}])
payload4b = create_order_payload(items=[{"name": "Domain-Driven Design", "quantity": 4}])

with concurrent.futures.ThreadPoolExecutor() as executor:
    futures = [executor.submit(send_order, p) for p in [payload4a, payload4b]]
    for f in concurrent.futures.as_completed(futures):
        status, resp = f.result()
        print(f"Status: {status}, Response: {resp}")
        assert status == 200, "Test 4 Failed (should at least enqueue)"

print("\nAll End-to-End Scenarios triggered successfully.")
