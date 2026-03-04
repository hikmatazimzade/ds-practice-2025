import sys
import os
import dns.resolver
from geopy.geocoders import Nominatim
from geopy.exc import GeocoderTimedOut
import grpc
from concurrent import futures

# gRPC Setup
FILE = __file__ if '__file__' in globals() else os.getenv("PYTHONFILE", "")
transaction_verification_grpc_path = os.path.abspath(os.path.join(FILE, '../../../utils/pb/transaction_verification'))
sys.path.insert(0, transaction_verification_grpc_path)
import transaction_verification_pb2 as transaction_verification
import transaction_verification_pb2_grpc as transaction_verification_grpc

# Initialize Geocoder
geolocator = Nominatim(user_agent="ds_practice_verifier")

DISPOSABLE_DOMAINS = ["yopmail.com", "tempmail.com", "guerrillamail.com", "sharklasers.com"]

def is_domain_valid(email):
    try:
        domain = email.split('@')[-1].lower()
        # Blacklist check
        if domain in DISPOSABLE_DOMAINS:
            return False
            
        resolver = dns.resolver.Resolver()
        resolver.lifetime = 2.0
        # Check for MX records to ensure the domain can receive emails
        records = dns.resolver.resolve(domain, 'MX')
        return True if records else False
    except (dns.resolver.NoAnswer, dns.resolver.NXDOMAIN, Exception):
        return False

class TransactionVerificationService(transaction_verification_grpc.TransactionVerificationServiceServicer):
    
    def TransactionVerification(self, request, context):
        is_valid = True
        error_message = "Transaction format verified."

        print(f"Verifying transaction for: {request.name}")

        # --- 1. BASIC FIELD VERIFICATION ---
        if not request.name.strip() or not request.contact.strip():
            is_valid = False
            error_message = "Name and contact info are required."
        
        elif request.itemsCount <= 0:
            is_valid = False
            error_message = "Transaction invalid: Cart is empty."

        # --- 2. SMART CARD CLEANING & CHECK ---
        # We strip spaces and dashes to be user-friendly
        clean_card = request.creditCard.replace(" ", "").replace("-", "")
        
        if not clean_card.isdigit():
            is_valid = False
            error_message = "Credit card must contain only digits."
        elif not (13 <= len(clean_card) <= 19):
            is_valid = False
            error_message = f"Invalid card length ({len(clean_card)} digits). Expected 13-19."
            
        # --- 3. CVV VERIFICATION ---
        elif not request.cvv.isdigit() or len(request.cvv) != 3:
            is_valid = False
            error_message = "Invalid CVV format (3 digits required)."

        # --- 4. EMAIL DOMAIN VERIFICATION ---
        if is_valid:
            if not is_domain_valid(request.contact):
                is_valid = False
                error_message = f"Invalid or disposable email domain: {request.contact.split('@')[-1]}"

        # --- 5. ADDRESS GEOLOCATION VERIFICATION (AI Layer) ---
        if is_valid:
            full_address = f"{request.street.strip()}, {request.city.strip()}, {request.state.strip()}, {request.zip.strip()}, {request.country.strip()}"          
            print(f"AI Geocoding search: {full_address}")
            try:
                location = geolocator.geocode(full_address, timeout=10)
                if location is None:
                    is_valid = False
                    error_message = f"Address not found by Geocoding API: {full_address}"
                else:
                    print(f"Real address found! Coordinates: {location.latitude}, {location.longitude}")
            except Exception as e:
                # We log the error but don't necessarily block the user if the external API is down
                print(f"Geocoding Service Error: {e}")

        return transaction_verification.TransactionVerificationResponse(
            is_valid=is_valid, 
            message=error_message
        )


def serve():
    server = grpc.server(futures.ThreadPoolExecutor())
    transaction_verification_grpc.add_TransactionVerificationServiceServicer_to_server(TransactionVerificationService(), server)
    port = "50052"
    server.add_insecure_port("[::]:" + port)
    server.start()
    print(f"Transaction Verification Server started. Listening on port {port}.")
    server.wait_for_termination()

if __name__ == '__main__':
    serve()