# AI Chatbot Maker

An AI-powered chatbot creation platform built with FastAPI, featuring web crawling, document processing, and vector storage capabilities.

## Prerequisites

- Python 3.12 or higher
- [uv](https://github.com/astral-sh/uv) package manager
- PostgreSQL database

## Setup

### 1. Install Dependencies

Sync all project dependencies using uv:

```bash
uv sync
```

This will create a virtual environment and install all required packages.

### 2. Environment Configuration

Create a `.env` file in the root directory with the following variables:

```env
# Database
DB_URL=postgres://postgres:password@localhost:5432/chatty_db

# JWT Configuration
JWT_SECRET=your-secret-key-here
JWT_ALGORITHM=HS256
JWT_EXPIRE_MINUTES=60

# App Configuration
APP_NAME=chatty
ENV=development
DEBUG=False

# API Configuration
API_KEY_HEADER=x-api-key
RATE_LIMIT_PER_MINUTE=60
```

**Note:** Replace the database URL and JWT secret with your actual values.

### 3. Database Setup

Ensure your PostgreSQL database is running and accessible. The application will automatically create tables on startup when `ENV=development`.

## Running the Application

### Using uv run (Recommended)

```bash
uv run uvicorn main:app --reload
```

### Direct Python Execution

```bash
python main.py
```

The application will start on `http://0.0.0.0:3009` by default.

## API Endpoints

- `GET /health` - Health check endpoint

## Development

The application uses:
- **FastAPI** for the web framework
- **Tortoise ORM** for database operations
- **Crawl4AI** for web crawling
- **LangChain** and **LlamaIndex** for AI processing
- **PostgreSQL** as the database

## Project Structure

```
crawl-test/
├── app/
│   ├── admin/          # Admin routes
│   ├── chatbot/        # Chatbot functionality
│   └── core/           # Core services and models
│       ├── config/     # Configuration files
│       ├── models/     # Database models
│       └── services/   # Business logic services
├── main.py             # Application entry point
└── pyproject.toml      # Project dependencies
```

## Notes

- The `.env` file is required for the application to run
- Database tables are auto-generated in development mode
- More documentation will be added as the project evolves

