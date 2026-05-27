import sys
import os
import threading
import grpc
import json
import uuid
import time
from flask import Flask, request
from flask_cors import CORS

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry import metrics
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter

# Setup OpenTelemetry Tracing
trace.set_tracer_provider(TracerProvider())
tracer = trace.get_tracer(__name__)
span_processor = BatchSpanProcessor(OTLPSpanExporter(endpoint="http://observability:4318/v1/traces"))
trace.get_tracer_provider().add_span_processor(span_processor)

# Setup OpenTelemetry Metrics
metric_reader = PeriodicExportingMetricReader(OTLPMetricExporter(endpoint="http://observability:4318/v1/metrics"))
metrics.set_meter_provider(MeterProvider(metric_readers=[metric_reader]))
meter = metrics.get_meter(__name__)

checkout_counter = meter.create_counter("checkout_requests_total", description="Total number of checkout requests")
checkout_histogram = meter.create_histogram("checkout_duration_seconds", description="Duration of checkout processing")
active_requests_counter = meter.create_up_down_counter("active_checkout_requests", description="Number of currently active checkout requests")

def get_active_threads(options):
    yield metrics.Observation(threading.active_count())

meter.create_observable_gauge("active_threads", callbacks=[get_active_threads], description="Number of active threads")

FILE = __file__ if '__file__' in globals() else os.getenv("PYTHONFILE", "")
utils_path = os.path.abspath(os.path.join(FILE, '../../../utils/pb'))
sys.path.insert(0, os.path.join(utils_path, 'fraud_detection'))
sys.path.insert(0, os.path.join(utils_path, 'suggestions'))
sys.path.insert(0, os.path.join(utils_path, 'transaction_verification'))
sys.path.insert(0, os.path.join(utils_path, 'order_queue'))

import fraud_detection_pb2 as fraud_pb2
import fraud_detection_pb2_grpc as fraud_grpc
import suggestions_pb2 as suggestions_pb2
import suggestions_pb2_grpc as suggestions_grpc
import transaction_verification_pb2 as tx_pb2
import transaction_verification_pb2_grpc as tx_grpc
import order_queue_pb2 as oq_pb2
import order_queue_pb2_grpc as oq_grpc

app = Flask(__name__, template_folder="../../../frontend/src")
CORS(app, resources={r'/*': {'origins': '*'}})

NUM_SERVICES = 3  # [fraud=0, transaction=1, suggestions=2]

@app.route('/', methods=['GET'])
def index():
    return "Orchestrator is running"

@app.route('/checkout', methods=['POST'])
def checkout():
    start_time = time.time()
    checkout_counter.add(1)
    
    with tracer.start_as_current_span("orchestrator_checkout") as span:
        request_data = json.loads(request.data)
        print("Request Data:", request_data)

        user = request_data.get('user', {})
        card_info = request_data.get('creditCard', {})
        addr_info = request_data.get('billingAddress', {})
        items = request_data.get('items', [])

        order_id = str(uuid.uuid4())
        span.set_attribute("order_id", order_id)
        span.set_attribute("items_count", len(items))
        print(f"[Orchestrator] Generated OrderID: {order_id}")

        vc_lock = threading.Lock()
        vector_clock = [0] * NUM_SERVICES
        failure = {"detected": False, "message": None}

    # -------------------------------------------------------------------------
    # PHASE 1 — InitOrder on all 3 services in parallel
    # -------------------------------------------------------------------------
    def init_transaction():
        try:
            with grpc.insecure_channel('transaction_verification:50052') as channel:
                stub = tx_grpc.TransactionVerificationServiceStub(channel)
                resp = stub.InitOrder(tx_pb2.TransactionVerificationRequest(
                    order_id=order_id,
                    name=user.get('name', ''),
                    contact=user.get('contact', ''),
                    creditCard=card_info.get('number', ''),
                    itemsCount=len(items),
                    cvv=card_info.get('cvv', ''),
                    street=addr_info.get('street', ''),
                    city=addr_info.get('city', ''),
                    zip=addr_info.get('zip', ''),
                    country=addr_info.get('country', ''),
                    state=addr_info.get('state', '')
                ))
                print(f"[Orchestrator] InitOrder -> transaction: {resp.success}")
        except Exception as e:
            print(f"[Orchestrator] InitOrder transaction error: {e}")

    def init_suggestions():
        try:
            with grpc.insecure_channel('suggestions:50053') as channel:
                stub = suggestions_grpc.SuggestionsServiceStub(channel)
                resp = stub.InitOrder(suggestions_pb2.InitRequest(
                    order_id=order_id,
                    user_id=user.get('name', 'anonymous'),
                    ordered_items=[i.get('name', '') for i in items]
                ))
                print(f"[Orchestrator] InitOrder -> suggestions: {resp.success}")
        except Exception as e:
            print(f"[Orchestrator] InitOrder suggestions error: {e}")

    def init_fraud():
        try:
            with grpc.insecure_channel('fraud_detection:50051') as channel:
                stub = fraud_grpc.FraudDetectionServiceStub(channel)
                resp = stub.InitOrder(fraud_pb2.FraudInitRequest(
                    order_id=order_id,
                    card_number=card_info.get('number', ''),
                    order_amount=float(len(items)),
                    name=user.get('name', ''),
                    contact=user.get('contact', '')
                ))
                print(f"[Orchestrator] InitOrder -> fraud: {resp.success}")
        except Exception as e:
            print(f"[Orchestrator] InitOrder fraud error: {e}")

    print(f"[Orchestrator] Phase 1 — InitOrder")
    t_init1 = threading.Thread(target=init_transaction)
    t_init2 = threading.Thread(target=init_suggestions)
    t_init3 = threading.Thread(target=init_fraud)
    t_init1.start(); t_init2.start(); t_init3.start()
    t_init1.join(); t_init2.join(); t_init3.join()
    print(f"[Orchestrator] Phase 1 done. VC: {vector_clock}")

    # -------------------------------------------------------------------------
    # PHASE 2 — Events a and b in parallel (transaction)
    # -------------------------------------------------------------------------
    def event_a():
        if failure["detected"]:
            return
        try:
            with grpc.insecure_channel('transaction_verification:50052') as channel:
                stub = tx_grpc.TransactionVerificationServiceStub(channel)
                with vc_lock:
                    vc_copy = list(vector_clock)
                resp = stub.VerifyItems(tx_pb2.VerifyRequest(
                    order_id=order_id,
                    vector_clock=vc_copy
                ))
                print(f"[Orchestrator] Event (a) VerifyItems — valid={resp.is_valid} VC={list(resp.vector_clock)}")
                with vc_lock:
                    for i in range(NUM_SERVICES):
                        vector_clock[i] = max(vector_clock[i], resp.vector_clock[i])
                if not resp.is_valid:
                    failure["detected"] = True
                    failure["message"] = resp.message
        except Exception as e:
            print(f"[Orchestrator] Event (a) error: {e}")
            failure["detected"] = True
            failure["message"] = str(e)

    def event_b():
        if failure["detected"]:
            return
        try:
            with grpc.insecure_channel('transaction_verification:50052') as channel:
                stub = tx_grpc.TransactionVerificationServiceStub(channel)
                with vc_lock:
                    vc_copy = list(vector_clock)
                resp = stub.VerifyUserData(tx_pb2.VerifyRequest(
                    order_id=order_id,
                    vector_clock=vc_copy
                ))
                print(f"[Orchestrator] Event (b) VerifyUserData — valid={resp.is_valid} VC={list(resp.vector_clock)}")
                with vc_lock:
                    for i in range(NUM_SERVICES):
                        vector_clock[i] = max(vector_clock[i], resp.vector_clock[i])
                if not resp.is_valid:
                    failure["detected"] = True
                    failure["message"] = resp.message
        except Exception as e:
            print(f"[Orchestrator] Event (b) error: {e}")
            failure["detected"] = True
            failure["message"] = str(e)

    print(f"[Orchestrator] Phase 2 — Events a ‖ b")
    ta = threading.Thread(target=event_a)
    tb = threading.Thread(target=event_b)
    ta.start(); tb.start()
    ta.join(); tb.join()
    print(f"[Orchestrator] Phase 2 done. VC: {vector_clock}")

    if failure["detected"]:
        return {"orderId": order_id, "status": "Order Rejected",
                "error": {"message": failure["message"]}, "suggestedBooks": []}, 400

    # -------------------------------------------------------------------------
    # PHASE 3 — Event c (transaction) and event d (fraud) in parallel
    # -------------------------------------------------------------------------
    def event_c():
        if failure["detected"]:
            return
        try:
            with grpc.insecure_channel('transaction_verification:50052') as channel:
                stub = tx_grpc.TransactionVerificationServiceStub(channel)
                with vc_lock:
                    vc_copy = list(vector_clock)
                resp = stub.VerifyCreditCard(tx_pb2.VerifyRequest(
                    order_id=order_id,
                    vector_clock=vc_copy
                ))
                print(f"[Orchestrator] Event (c) VerifyCreditCard — valid={resp.is_valid} VC={list(resp.vector_clock)}")
                with vc_lock:
                    for i in range(NUM_SERVICES):
                        vector_clock[i] = max(vector_clock[i], resp.vector_clock[i])
                if not resp.is_valid:
                    failure["detected"] = True
                    failure["message"] = resp.message
        except Exception as e:
            print(f"[Orchestrator] Event (c) error: {e}")
            failure["detected"] = True
            failure["message"] = str(e)

    def event_d():
        if failure["detected"]:
            return
        try:
            with grpc.insecure_channel('fraud_detection:50051') as channel:
                stub = fraud_grpc.FraudDetectionServiceStub(channel)
                with vc_lock:
                    vc_copy = list(vector_clock)
                resp = stub.CheckUserData(fraud_pb2.FraudCheckRequest(
                    order_id=order_id,
                    vector_clock=vc_copy
                ))
                print(f"[Orchestrator] Event (d) CheckUserData — is_fraud={resp.is_fraud} VC={list(resp.vector_clock)}")
                with vc_lock:
                    for i in range(NUM_SERVICES):
                        vector_clock[i] = max(vector_clock[i], resp.vector_clock[i])
                if resp.is_fraud:
                    failure["detected"] = True
                    failure["message"] = resp.message
        except Exception as e:
            print(f"[Orchestrator] Event (d) error: {e}")
            failure["detected"] = True
            failure["message"] = str(e)

    print(f"[Orchestrator] Phase 3 — Events c ‖ d")
    tc = threading.Thread(target=event_c)
    td = threading.Thread(target=event_d)
    tc.start(); td.start()
    tc.join(); td.join()
    print(f"[Orchestrator] Phase 3 done. VC: {vector_clock}")

    if failure["detected"]:
        return {"orderId": order_id, "status": "Order Rejected",
                "error": {"message": failure["message"]}, "suggestedBooks": []}, 400

    # -------------------------------------------------------------------------
    # PHASE 4 — Event e (fraud: check card) — after both c and d
    # -------------------------------------------------------------------------
    def event_e():
        if failure["detected"]:
            return
        try:
            with grpc.insecure_channel('fraud_detection:50051') as channel:
                stub = fraud_grpc.FraudDetectionServiceStub(channel)
                with vc_lock:
                    vc_copy = list(vector_clock)
                resp = stub.CheckCardFraud(fraud_pb2.FraudCheckRequest(
                    order_id=order_id,
                    vector_clock=vc_copy
                ))
                print(f"[Orchestrator] Event (e) CheckCardFraud — is_fraud={resp.is_fraud} VC={list(resp.vector_clock)}")
                with vc_lock:
                    for i in range(NUM_SERVICES):
                        vector_clock[i] = max(vector_clock[i], resp.vector_clock[i])
                if resp.is_fraud:
                    failure["detected"] = True
                    failure["message"] = resp.message
        except Exception as e:
            print(f"[Orchestrator] Event (e) error: {e}")
            failure["detected"] = True
            failure["message"] = str(e)

    print(f"[Orchestrator] Phase 4 — Event e")
    te = threading.Thread(target=event_e)
    te.start(); te.join()
    print(f"[Orchestrator] Phase 4 done. VC: {vector_clock}")

    if failure["detected"]:
        return {"orderId": order_id, "status": "Order Rejected",
                "error": {"message": failure["message"]}, "suggestedBooks": []}, 400

    # -------------------------------------------------------------------------
    # PHASE 5 — Event f (suggestions) — after e
    # -------------------------------------------------------------------------
    suggested_books = []

    def event_f():
        try:
            with grpc.insecure_channel('suggestions:50053') as channel:
                stub = suggestions_grpc.SuggestionsServiceStub(channel)
                with vc_lock:
                    vc_copy = list(vector_clock)
                resp = stub.GetSuggestions(suggestions_pb2.SuggestionRequest(
                    order_id=order_id,
                    vector_clock=vc_copy
                ))
                print(f"[Orchestrator] Event (f) GetSuggestions — success={resp.success} VC={list(resp.vector_clock)}")
                with vc_lock:
                    for i in range(NUM_SERVICES):
                        vector_clock[i] = max(vector_clock[i], resp.vector_clock[i])
                for b in resp.suggestions:
                    suggested_books.append({"bookId": b.book_id, "title": b.title, "author": b.author})
        except Exception as e:
            print(f"[Orchestrator] Event (f) error: {e}")

    print(f"[Orchestrator] Phase 5 — Event f")
    tf = threading.Thread(target=event_f)
    tf.start(); tf.join()
    print(f"[Orchestrator] Phase 5 done. Final VC: {vector_clock}")

    # -------------------------------------------------------------------------
    # PHASE 6 — Enqueue approved order
    # -------------------------------------------------------------------------
    try:
        with grpc.insecure_channel('order_queue:50055') as channel:
            stub = oq_grpc.OrderQueueServiceStub(channel)
            enqueue_resp = stub.Enqueue(oq_pb2.EnqueueRequest(
                order=oq_pb2.Order(
                    order_id=order_id,
                    items=[oq_pb2.OrderItem(name=i.get('name', ''), quantity=i.get('quantity', 1)) for i in items],
                    user_name=user.get('name', ''),
                    total_amount=float(len(items) * 30)
                )
            ))
            print(f"[Orchestrator] Order enqueued: {enqueue_resp.success}")
    except Exception as e:
        print(f"[Orchestrator] Enqueue error: {e}")

    checkout_histogram.record(time.time() - start_time)
    return {
        "orderId": order_id,
        "status": "Order Approved",
        "suggestedBooks": suggested_books
    }

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)