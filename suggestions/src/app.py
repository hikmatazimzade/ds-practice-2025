import sys
import os

# Import the generated gRPC code
FILE = __file__ if '__file__' in dir() else os.getenv('PYTHONSTARTUP', '')
sys.path.insert(0, os.path.join(os.path.dirname(FILE), '../../utils/pb/suggestions'))

import grpc
from concurrent import futures
import logging
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


class SuggestionsServicer(suggestions_pb2_grpc.SuggestionsServiceServicer):
    def GetSuggestions(self, request, context):
        logger.info(f"Received suggestion request for user_id='{request.user_id}', "
                    f"ordered_items={list(request.ordered_items)}")

        # Filter out books the user is already ordering
        ordered_titles = set(item.lower() for item in request.ordered_items)
        available = [
            b for b in BOOK_CATALOG
            if b["title"].lower() not in ordered_titles
        ]

        # Return up to 3 suggestions
        suggestions = available[:3]

        logger.info(f"Returning {len(suggestions)} suggestions: "
                    f"{[b['title'] for b in suggestions]}")

        return suggestions_pb2.SuggestionResponse(
            suggestions=[
                suggestions_pb2.Book(
                    book_id=b["book_id"],
                    title=b["title"],
                    author=b["author"]
                )
                for b in suggestions
            ]
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