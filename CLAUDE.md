# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Django REST Framework JSON:API (DJA) is a Django REST framework adapter that implements the JSON:API specification. The package transforms standard Django REST framework responses into JSON:API-compliant format with proper resource types, relationships, links, and included resources.

**Core Goals:**
- Compliance with the JSON:API spec
- Maximum compatibility with Django REST framework
- Sane defaults for easy adoption
- High test coverage and performance

**Supported Versions:**
- Python: 3.8, 3.9, 3.10, 3.11, 3.12
- Django: 4.2, 5.0, 5.1
- Django REST framework: 3.14, 3.15

## Development Commands

### Setup and Installation
```bash
# Install development dependencies
pip install -Ur requirements.txt

# Install package in editable mode
pip install -e .
```

### Running Tests
```bash
# Run all tests with pytest
pytest

# Run tests with coverage
pytest --cov --no-cov-on-fail --cov-report xml

# Run specific test file
pytest tests/test_views.py

# Run specific test
pytest tests/test_views.py::test_function_name

# Run tests with tox (all Python/Django/DRF combinations)
tox

# Run tests for specific environment
tox -e py310-django50-drf315
```

### Code Quality
```bash
# Format code with black
black .

# Check formatting
black --check .

# Run linting
flake8

# Sort imports
isort .

# Run all pre-commit hooks
pre-commit run --all-files
```

### Documentation
```bash
# Build documentation locally
sphinx-build -W -b html -d docs/_build/doctrees docs docs/_build/html

# Or with tox
tox -e docs
```

### Example Application
```bash
# Set up example app database
django-admin migrate --settings=example.settings

# Load fixtures
django-admin loaddata drf_example --settings=example.settings

# Run development server
django-admin runserver --settings=example.settings

# Access at:
# http://localhost:8000 - API root
# http://localhost:8000/swagger-ui/ - Swagger UI
# http://localhost:8000/openapi - OpenAPI spec
```

## Architecture

### Core Components

**rest_framework_json_api/** - Main package implementing JSON:API specification:

- **serializers.py**: Core serializers that extend DRF serializers
  - `ResourceIdentifierObjectSerializer`: Handles resource identifier objects for relationships
  - `ModelSerializer`/`HyperlinkedModelSerializer`: JSON:API-aware serializers
  - Automatic field name formatting (camelCase/dasherize) and type pluralization

- **renderers.py**: Transform DRF data into JSON:API format
  - `JSONRenderer`: Main renderer with `render()` method for JSON:API document structure
  - `BrowsableAPIRenderer`: Browsable API support
  - Handles resource objects, relationships, included resources, links, and meta

- **parsers.py**: Parse incoming JSON:API requests
  - `JSONParser`: Parses JSON:API request payloads into DRF-compatible format
  - Handles resource objects, relationships, and client-generated IDs

- **views.py**: ViewSet mixins and helpers
  - `PreloadIncludesMixin`: Optimize queries by prefetching/selecting related data based on `?include=` parameter
  - `RelationshipView`: Handle relationship endpoints for JSON:API relationships
  - Methods for handling `?include`, resource linkage, and sparse fieldsets

- **relations.py**: Relationship field implementations
  - `ResourceRelatedField`: JSON:API-aware relationship field
  - Handles resource linkage and relationship objects

- **pagination.py**: JSON:API pagination
  - `JsonApiPageNumberPagination`: Implements JSON:API-compliant pagination links

- **filters.py**: Query parameter filtering
  - `QueryParameterValidationFilter`: Validates JSON:API query parameters
  - `OrderingFilter`: JSON:API-compliant sorting with `sort` parameter

- **exceptions.py**: JSON:API error handling
  - `exception_handler()`: Formats errors per JSON:API error object spec
  - `Conflict`: 409 Conflict exception for resource conflicts

- **metadata.py**: OPTIONS request metadata
  - `JSONAPIMetadata`: Returns JSON:API-compliant metadata

- **utils.py**: Shared utilities
  - Resource type resolution (from models, serializers, instances)
  - Field name formatting (dasherize, camelize, underscore)
  - Include parameter parsing
  - Hyperlink and relationship utilities

- **schemas/openapi.py**: OpenAPI schema generation for JSON:API endpoints

- **django_filters/**: Integration with django-filter package

### Key Design Patterns

**Resource Type Determination**: The library uses a hierarchy to determine resource types:
1. `resource_name` attribute on serializer Meta
2. Model name (formatted per `JSON_API_FORMAT_TYPES` setting)
3. Serializer name minus "Serializer" suffix

**Include System**: The `?include=` parameter allows clients to request related resources. Implementations should:
- Use `PreloadIncludesMixin` to define `prefetch_for_includes` and `select_for_includes` to optimize queries
- Configure what relationships can be included to prevent N+1 queries

**Field Name Formatting**: Controlled by settings:
- `JSON_API_FORMAT_FIELD_NAMES`: Format field names (camelCase, dasherize, etc.)
- `JSON_API_FORMAT_TYPES`: Format resource type names
- `JSON_API_PLURALIZE_TYPES`: Auto-pluralize type names

**Relationship Handling**: Two approaches:
1. `ResourceRelatedField`: For representing relationships in serializers
2. `RelationshipView`: For dedicated relationship endpoints

### Testing Structure

**tests/** - Test modules using pytest:
- `conftest.py`: Shared fixtures (models, clients, instances)
- `test_*.py`: Test modules organized by component
- Uses `pytest-django` with `DJANGO_SETTINGS_MODULE=example.settings.test`
- In-memory SQLite database for tests
- Factory Boy for test data generation

**example/** - Example Django project demonstrating usage:
- Full Django app with models, serializers, views
- Used for both manual testing and pytest integration tests
- Settings in `example/settings/` (dev.py, test.py)

### Settings Configuration

The package expects Django settings to configure REST framework with JSON:API components:

```python
REST_FRAMEWORK = {
    'DEFAULT_PARSER_CLASSES': ('rest_framework_json_api.parsers.JSONParser', ...),
    'DEFAULT_RENDERER_CLASSES': ('rest_framework_json_api.renderers.JSONRenderer', ...),
    'DEFAULT_PAGINATION_CLASS': 'rest_framework_json_api.pagination.JsonApiPageNumberPagination',
    'DEFAULT_METADATA_CLASS': 'rest_framework_json_api.metadata.JSONAPIMetadata',
    'EXCEPTION_HANDLER': 'rest_framework_json_api.exceptions.exception_handler',
    'DEFAULT_FILTER_BACKENDS': (
        'rest_framework_json_api.filters.QueryParameterValidationFilter',
        'rest_framework_json_api.filters.OrderingFilter',
        'rest_framework_json_api.django_filters.DjangoFilterBackend',
        'rest_framework.filters.SearchFilter',
    ),
    'SEARCH_PARAM': 'filter[search]',
}
```

Additional JSON:API specific settings:
- `JSON_API_FORMAT_FIELD_NAMES`: Field name formatting strategy
- `JSON_API_FORMAT_TYPES`: Resource type formatting strategy
- `JSON_API_PLURALIZE_TYPES`: Whether to pluralize type names

## Important Notes

- The package is designed as a drop-in replacement for `rest_framework.serializers` - the serializers module re-exports all DRF serializers
- Always use `pytest` for running tests, not Django's test runner
- The example app and tests share settings in development; tests override specific settings in `conftest.py`
- Pre-commit hooks run black, isort, and flake8 automatically
- When modifying core behavior, ensure JSON:API spec compliance: https://jsonapi.org/format/
- The package uses inflection library for automatic name transformations
