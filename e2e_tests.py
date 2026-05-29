import requests
import json
import concurrent.futures
import time

ORCHESTRATOR_URL = "http://localhost:8081/checkout"

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
        try:
            return response.status_code, response.json()
        except:
            return response.status_code, response.text
    except Exception as e:
        return 500, str(e)

print("================================================================")
print("🚀 DISTRIBUTED BOOKSHOP - END-TO-END TEST SUITE")
print("================================================================")

# 1. Single non-fraudulent order
print("\n[Test 1] 🟢 Single valid order")
print("Action: Sending a standard order for 'Learning Python'...")
payload1 = create_order_payload(items=[{"name": "Learning Python", "quantity": 1}])
status1, resp1 = send_order(payload1)
if isinstance(resp1, dict):
    print(f"Result: HTTP {status1} | OrderID: {resp1.get('orderId')}")
else:
    print(f"Result: HTTP {status1} | Error: {resp1}")
assert status1 == 200, "Test 1 Failed"

# 2. Multiple non-fraudulent non-conflicting orders
print("\n[Test 2] 🟢 Parallel valid orders")
print("Action: Sending 2 orders simultaneously for different books...")
payload2a = create_order_payload(items=[{"name": "JavaScript - The Good Parts", "quantity": 1}])
payload2b = create_order_payload(items=[{"name": "Clean Code", "quantity": 1}])

with concurrent.futures.ThreadPoolExecutor() as executor:
    futures = [executor.submit(send_order, p) for p in [payload2a, payload2b]]
    for f in concurrent.futures.as_completed(futures):
        status, resp = f.result()
        if isinstance(resp, dict):
            print(f"Result: HTTP {status} | OrderID: {resp.get('orderId')}")
        else:
            print(f"Result: HTTP {status} | Error: {resp}")
        assert status == 200, "Test 2 Failed"

# 3. Fraudulent vs Valid
print("\n[Test 3] 🔴 Mixed Fraudulent and Valid orders")
print("Action: Sending one fraud (invalid email) and one valid order...")
payload3a = create_order_payload(contact="invalid-email") # Fraud (no @)
payload3b = create_order_payload(items=[{"name": "Learning Python", "quantity": 1}]) # Valid

with concurrent.futures.ThreadPoolExecutor() as executor:
    f1 = executor.submit(send_order, payload3a)
    f2 = executor.submit(send_order, payload3b)
    
    status3a, resp3a = f1.result()
    status3b, resp3b = f2.result()
    if isinstance(resp3a, dict):
        print(f"Fraud Result: HTTP {status3a} (Expected 400) | Msg: {resp3a.get('error', {}).get('message')}")
    else:
        print(f"Fraud Result: HTTP {status3a} (Expected 400) | Error: {resp3a}")
        
    if isinstance(resp3b, dict):
        print(f"Valid Result: HTTP {status3b} (Expected 200) | OrderID: {resp3b.get('orderId')}")
    else:
        print(f"Valid Result: HTTP {status3b} (Expected 200) | Error: {resp3b}")
        
    assert status3a == 400, "Test 3a (Fraud) Failed"
    assert status3b == 200, "Test 3b (Valid) Failed"

# 4. Conflicting orders (2PC Demonstration)
print("\n[Test 4] ⚠️ Conflicting orders (Stock depletion)")
print("Action: Sending 2 orders for 'Domain-Driven Design' (Stock: 7).")
print("        Each order requests 4 units. Total 8 > 7 available.")
print("        Both will be enqueued, but the 2nd should be ABORTED by 2PC.")
payload4a = create_order_payload(items=[{"name": "Domain-Driven Design", "quantity": 4}])
payload4b = create_order_payload(items=[{"name": "Domain-Driven Design", "quantity": 4}])

with concurrent.futures.ThreadPoolExecutor() as executor:
    futures = [executor.submit(send_order, p) for p in [payload4a, payload4b]]
    for f in concurrent.futures.as_completed(futures):
        status, resp = f.result()
        if isinstance(resp, dict):
            print(f"Enqueue Result: HTTP {status} | OrderID: {resp.get('orderId')}")
        else:
            print(f"Enqueue Result: HTTP {status} | Error: {resp}")
        assert status == 200, "Test 4 Failed (should at least enqueue)"

print("\n================================================================")
print("✅ ALL TESTS TRIGGERED SUCCESSFULLY")
print("================================================================")
print("\n🔍 HOW TO VERIFY DISTRIBUTED BEHAVIOR:")
print("1. VECTOR CLOCKS: Check 'orchestrator' logs for 'Final VC'.")
print("   It should show [2, 3, 1] for successful orders.")
print("2. BULLY ALGORITHM: Check 'order_executor_3' logs.")
print("   It should say 'I am the new LEADER'.")
print("3. 2-PHASE COMMIT (2PC): Check 'order_executor_3' logs for Test 4.")
print("   One transaction will show 'COMMIT', the other 'ABORT' due to stock.")
print("4. OBSERVABILITY: Visit http://localhost:3000 for Grafana Dashboards.")
print("================================================================")
