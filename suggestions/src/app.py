import sys
import os
import grpc
import logging
from concurrent import futures

FILE = __file__ if '__file__' in dir() else os.getenv('PYTHONSTARTUP', '')
sys.path.insert(0, os.path.join(os.path.dirname(FILE), '../../utils/pb/suggestions'))

import suggestions_pb2
import suggestions_pb2_grpc

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format='[SuggestionsService] %(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Static book catalog
BOOK_CATALOG = [
    {"book_id": "1", "title": "The Pragmatic Programmer", "author": "Andrew Hunt"},
    {"book_id": "2", "title": "Clean Code", "author": "Robert C. Martin"},
    {"book_id": "3", "title": "Designing Data-Intensive Applications", "author": "Martin Kleppmann"},
    {"book_id": "4", "title": "The Hitchhiker's Guide to the Galaxy", "author": "Douglas Adams"},
    {"book_id": "5", "title": "Dune", "author": "Frank Herbert"},
    {"book_id": "6", "title": "1984", "author": "George Orwell"},
    {"book_id": "7", "title": "Foundation", "author": "Isaac Asimov"},
    {"book_id": "8", "title": "The Name of the Wind", "author": "Patrick Rothfuss"},
    {"book_id": "9", "title": "Neuromancer", "author": "William Gibson"},
    {"book_id": "10", "title": "Snow Crash", "author": "Neal Stephenson"},
]

# Suggestions is index 2 → [fraud=0, transaction=1, suggestions=2]
SERVICE_INDEX = 2
NUM_SERVICES = 3

# In-memory cache: { order_id: { "user_id": ..., "ordered_items": [...], "vc": [0,0,0] } }
orders = {}


def merge_and_increment(local_vc, incoming_vc):
    """Merge incoming VC with local, then increment our own slot."""
    merged = [max(local_vc[i], incoming_vc[i]) for i in range(NUM_SERVICES)]
    merged[SERVICE_INDEX] += 1
    return merged


class SuggestionsServicer(suggestions_pb2_grpc.SuggestionsServiceServicer):

    def InitOrder(self, request, context):
        """
        Called by orchestrator to cache order data and initialize vector clock.
        This happens BEFORE any events are triggered.
        """
        orders[request.order_id] = {
            "user_id": request.user_id,
            "ordered_items": list(request.ordered_items),
            "vc": [0] * NUM_SERVICES
        }
        logger.info(f"[OrderID: {request.order_id}] Order initialized. "
                    f"VC: {orders[request.order_id]['vc']}")
        return suggestions_pb2.InitResponse(success=True)

    def GetSuggestions(self, request, context):
        """
        Event f — generate book suggestions.
        Depends on fraud event e completing first.
        Receives vector clock from orchestrator, merges it, increments own slot.
        """
        order_id = request.order_id
        incoming_vc = list(request.vector_clock)

        logger.info(f"[OrderID: {order_id}] Event f triggered. "
                    f"Incoming VC: {incoming_vc}")

        # Check order exists in cache
        if order_id not in orders:
            logger.error(f"[OrderID: {order_id}] Order not found in cache!")
            return suggestions_pb2.SuggestionResponse(
                success=False,
                error="Order not found"
            )

        entry = orders[order_id]

        # Merge incoming VC with local, then increment our slot
        entry["vc"] = merge_and_increment(entry["vc"], incoming_vc)
        logger.info(f"[OrderID: {order_id}] Event f - GetSuggestions. "
                    f"Updated VC: {entry['vc']}")

        # Filter out books already being ordered
        ordered_titles = set(item.lower() for item in entry["ordered_items"])
        available = [
            b for b in BOOK_CATALOG
            if b["title"].lower() not in ordered_titles
        ]

        # Return top 3 suggestions
        suggestions = available[:3]

        logger.info(f"[OrderID: {order_id}] Returning {len(suggestions)} suggestions: "
                    f"{[b['title'] for b in suggestions]}")

        return suggestions_pb2.SuggestionResponse(
            suggestions=[
                suggestions_pb2.Book(
                    book_id=b["book_id"],
                    title=b["title"],
                    author=b["author"]
                )
                for b in suggestions
            ],
            vector_clock=entry["vc"],
            success=True
        )


def serve():
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    suggestions_pb2_grpc.add_SuggestionsServiceServicer_to_server(
        SuggestionsServicer(), server
    )
    port = "50053"
    server.add_insecure_port(f"[::]:{port}")
    server.start()
    logger.info(f"Suggestions gRPC server started on port {port}")
    server.wait_for_termination()


if __name__ == "__main__":
    serve()