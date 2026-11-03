import sys
from .service import Service

if __name__ == "__main__":
    Service(sys.argv[1]).execute_approved(sys.argv[2])
