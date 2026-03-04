import sys
import os
import threading
import grpc
import json
from flask import Flask, request
from flask_cors import CORS

FILE = __file__ if '__file__' in globals() else os.getenv("PYTHONFILE", "")

# Configuration des chemins gRPC
utils_path = os.path.abspath(os.path.join(FILE, '../../../utils/pb'))
sys.path.insert(0, os.path.join(utils_path, 'fraud_detection'))
sys.path.insert(0, os.path.join(utils_path, 'suggestions'))
sys.path.insert(0, os.path.join(utils_path, 'transaction_verification'))

import fraud_detection_pb2 as fraud_detection
import fraud_detection_pb2_grpc as fraud_detection_grpc
import suggestions_pb2 as suggestions
import suggestions_pb2_grpc as suggestions_grpc 
import transaction_verification_pb2 as transaction_verification
import transaction_verification_pb2_grpc as transaction_verification_grpc

app = Flask(__name__, template_folder="../../../frontend/src")
CORS(app, resources={r'/*': {'origins': '*'}})

# --- FONCTIONS WRAPPERS gRPC ---

def check_fraud(card_number, amount):
    with grpc.insecure_channel('fraud_detection:50051') as channel:
        stub = fraud_detection_grpc.FraudDetectionServiceStub(channel)
        # On utilise card_number ou creditCard selon ton .proto
        request_msg = fraud_detection.FraudRequest(
            card_number=str(card_number), 
            order_amount=float(amount)
        )
        return stub.CheckFraud(request_msg)

def verify_transaction(name, contact, card, items_count, cvv, street, city, zip_code, country, state):
    with grpc.insecure_channel('transaction_verification:50052') as channel:
        stub = transaction_verification_grpc.TransactionVerificationServiceStub(channel)
        request_msg = transaction_verification.TransactionVerificationRequest(
            name=name, contact=contact, creditCard=card, itemsCount=items_count,
            cvv=cvv, street=street, city=city, zip=zip_code, country=country, state=state
        )
        return stub.TransactionVerification(request_msg)

def get_suggestions(user_name, item_names):
    try:
        with grpc.insecure_channel('suggestions:50053') as channel:
            stub = suggestions_grpc.SuggestionsServiceStub(channel)
            response = stub.GetSuggestions(suggestions.SuggestionRequest(
                user_id=user_name,
                ordered_items=item_names
            ))
            return [{"bookId": b.book_id, "title": b.title, "author": b.author} for b in response.suggestions]
    except Exception as e:
        print(f"Suggestions error: {e}")
        return []

# --- ROUTES FLASK ---

@app.route('/', methods=['GET'])
def index():
    return "Orchestrator is running"

@app.route('/checkout', methods=['POST'])
def checkout():
    request_data = json.loads(request.data)
    
    # 1. Extraction des données
    user = request_data.get('user', {})
    card_info = request_data.get('creditCard', {})
    addr_info = request_data.get('billingAddress', {})
    items = request_data.get('items', [])
    
    # --- ÉTAPE A : VERIFICATION (Port 50052) ---
    v_res = verify_transaction(
        user.get('name', ''), user.get('contact', ''), 
        card_info.get('number', ''), len(items), 
        card_info.get('cvv', ''), addr_info.get('street', ''),
        addr_info.get('city', ''), addr_info.get('zip', ''),
        addr_info.get('country', ''), addr_info.get('state', '')
    )
    
    if not v_res.is_valid:
            # ICI : On met le message dans un objet "error" pour le Frontend
            return {
                "status": "Order Rejected",
                "orderId": "0",
                "error": {"message": v_res.message}, 
                "suggestedBooks": []
            }, 400

    # --- ÉTAPE B : FRAUDE (Port 50051) ---
    amount = len(items) * 30 # Prix fictif
    f_res = check_fraud(card_info.get('number', ''), amount)

    if f_res.is_fraud:
        return {'orderId': '0', 'status': f"Order Rejected: Fraud Detected", 'suggestedBooks': []}, 400
    
    # --- ÉTAPE C : SUGGESTIONS (Port 50053) ---
    # On récupère les suggestions pour l'utilisateur final
    suggested_books = get_suggestions(user.get('name', 'anonymous'), [i.get('name', '') for i in items])

    # --- ÉTAPE D : APPROBATION FINALE ---
    return {
        'orderId': '12345', 
        'status': 'Order Approved', 
        'suggestedBooks': suggested_books
    }

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)