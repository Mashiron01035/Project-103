"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 21.11.2025.
    File        : base
    Project     : Project-103
    Description : Base classes and mixins for all models. This module provides base functionality for all data models.

    SPDX-License-Identifier: AGPL-3.0

"""

import hashlib
import json
import time
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, TypeVar

T = TypeVar("T")


@dataclass
class BaseModel(ABC):
    """Base class for all models."""

    def to_dict(self) -> Dict[str, Any]:
        """
        Convert model to dictionary.

        Returns:
            Dict: Model data as dictionary
        """
        return asdict(self)

    def to_json(self, indent: Optional[int] = None) -> str:
        """
        Convert model to JSON string.

        Args:
            indent: JSON indentation

        Returns:
            str: JSON string representation
        """
        return json.dumps(self.to_dict(), default=str, indent=indent)

    @classmethod
    @abstractmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BaseModel":
        """
        Create model from dictionary.

        Args:
            data: Dictionary with model data

        Returns:
            BaseModel: Model instance
        """
        pass

    @classmethod
    def from_json(cls, json_str: str) -> "BaseModel":
        """
        Create model from JSON string.

        Args:
            json_str: JSON string

        Returns:
            BaseModel: Model instance
        """
        data = json.loads(json_str)
        return cls.from_dict(data)

    def validate(self) -> List[str]:
        """
        Validate model data.

        Returns:
            List[str]: List of validation errors
        """
        return []

    def is_valid(self) -> bool:
        """
        Check if model is valid.

        Returns:
            bool: True if valid
        """
        return len(self.validate()) == 0

    def __hash__(self) -> int:
        """Generate hash for use in sets and dicts."""
        return hash(self.to_json())

    def copy(self) -> "BaseModel":
        """
        Create a copy of the model.

        Returns:
            BaseModel: Copy of the model
        """
        return self.__class__.from_dict(self.to_dict())


class TimestampMixin:
    """Mixin for models with timestamps."""

    @property
    def created_datetime(self) -> Optional[datetime]:
        """Get creation datetime."""
        if hasattr(self, "created_at") and self.created_at:
            return datetime.fromtimestamp(self.created_at / 1000)
        return None

    @property
    def updated_datetime(self) -> Optional[datetime]:
        """Get update datetime."""
        if hasattr(self, "updated_at") and self.updated_at:
            return datetime.fromtimestamp(self.updated_at / 1000)
        return None

    @property
    def age_seconds(self) -> int:
        """Get age in seconds."""
        if hasattr(self, "created_at") and self.created_at:
            return int((time.time() * 1000 - self.created_at) / 1000)
        return 0

    @property
    def age_minutes(self) -> float:
        """Get age in minutes."""
        return self.age_seconds / 60

    @property
    def age_hours(self) -> float:
        """Get age in hours."""
        return self.age_seconds / 3600

    def is_fresh(self, max_age_seconds: int) -> bool:
        """
        Check if data is fresh.

        Args:
            max_age_seconds: Maximum age in seconds

        Returns:
            bool: True if fresh
        """
        return self.age_seconds <= max_age_seconds


class PriceMixin:
    """Mixin for models with prices."""

    @staticmethod
    def round_price(price: float, precision: int = 8) -> float:
        """
        Round price to precision.

        Args:
            price: Price value
            precision: Decimal precision

        Returns:
            float: Rounded price
        """
        if precision == 0:
            return round(price)
        return round(price, precision)

    @staticmethod
    def calculate_percentage_change(old_price: float, new_price: float) -> float:
        """
        Calculate percentage change between prices.

        Args:
            old_price: Old price
            new_price: New price

        Returns:
            float: Percentage change
        """
        if old_price == 0:
            return 0
        return ((new_price - old_price) / old_price) * 100

    @staticmethod
    def calculate_price_difference(price1: float, price2: float) -> float:
        """
        Calculate absolute price difference.

        Args:
            price1: First price
            price2: Second price

        Returns:
            float: Absolute difference
        """
        return abs(price1 - price2)

    @staticmethod
    def calculate_spread(bid: float, ask: float) -> float:
        """
        Calculate spread between bid and ask.

        Args:
            bid: Bid price
            ask: Ask price

        Returns:
            float: Spread value
        """
        return ask - bid

    @staticmethod
    def calculate_spread_percentage(bid: float, ask: float) -> float:
        """
        Calculate spread percentage.

        Args:
            bid: Bid price
            ask: Ask price

        Returns:
            float: Spread percentage
        """
        if bid == 0:
            return 0
        return ((ask - bid) / bid) * 100


class SerializableMixin:
    """Mixin for serialization capabilities."""

    def to_redis(self) -> str:
        """
        Serialize for Redis storage.

        Returns:
            str: Serialized string
        """
        return self.to_json()

    @classmethod
    def from_redis(cls, data: str) -> "SerializableMixin":
        """
        Deserialize from Redis.

        Args:
            data: Serialized string

        Returns:
            SerializableMixin: Model instance
        """
        return cls.from_json(data)

    def to_database(self) -> Dict[str, Any]:
        """
        Prepare for database storage.

        Returns:
            Dict: Database-ready dictionary
        """
        data = self.to_dict()
        # Convert lists and nested objects to JSON
        for key, value in data.items():
            if isinstance(value, (list, dict)) and not isinstance(value, str):
                data[key] = json.dumps(value)
        return data

    @classmethod
    def from_database(cls, data: Dict[str, Any]) -> "SerializableMixin":
        """
        Create from database record.

        Args:
            data: Database record

        Returns:
            SerializableMixin: Model instance
        """
        # Parse JSON fields
        for key, value in data.items():
            if isinstance(value, str) and value.startswith(("[", "{")):
                try:
                    data[key] = json.loads(value)
                except json.JSONDecodeError:
                    pass
        return cls.from_dict(data)

    def to_message(self) -> Dict[str, Any]:
        """
        Prepare for message queue.

        Returns:
            Dict: Message-ready dictionary
        """
        return {
            "type": self.__class__.__name__,
            "data": self.to_dict(),
            "timestamp": int(time.time() * 1000),
        }

    def get_hash(self) -> str:
        """
        Get SHA256 hash of the model.

        Returns:
            str: SHA256 hash
        """
        return hashlib.sha256(self.to_json().encode()).hexdigest()


@dataclass
class ValidationError:
    """Validation error details."""

    field: str
    message: str
    value: Any = None

    def __str__(self) -> str:
        """String representation."""
        if self.value is not None:
            return f"{self.field}: {self.message} (value: {self.value})"
        return f"{self.field}: {self.message}"

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {"field": self.field, "message": self.message, "value": self.value}


class ValidatorMixin:
    """Mixin for advanced validation."""

    def validate_required_fields(self, fields: List[str]) -> List[ValidationError]:
        """
        Validate required fields are present.

        Args:
            fields: List of required field names

        Returns:
            List[ValidationError]: Validation errors
        """
        errors = []
        for field_name in fields:
            if not hasattr(self, field_name) or getattr(self, field_name) is None:
                errors.append(ValidationError(field=field_name, message="Field is required"))
        return errors

    def validate_positive_numbers(self, fields: List[str]) -> List[ValidationError]:
        """
        Validate fields are positive numbers.

        Args:
            fields: List of field names

        Returns:
            List[ValidationError]: Validation errors
        """
        errors = []
        for field_name in fields:
            if hasattr(self, field_name):
                value = getattr(self, field_name)
                if value is not None and value <= 0:
                    errors.append(
                        ValidationError(
                            field=field_name, message="Value must be positive", value=value
                        )
                    )
        return errors

    def validate_range(
        self, field: str, min_value: Optional[float] = None, max_value: Optional[float] = None
    ) -> Optional[ValidationError]:
        """
        Validate field is within range.

        Args:
            field: Field name
            min_value: Minimum value
            max_value: Maximum value

        Returns:
            Optional[ValidationError]: Validation error if invalid
        """
        if hasattr(self, field):
            value = getattr(self, field)
            if value is not None:
                if min_value is not None and value < min_value:
                    return ValidationError(
                        field=field, message=f"Value must be >= {min_value}", value=value
                    )
                if max_value is not None and value > max_value:
                    return ValidationError(
                        field=field, message=f"Value must be <= {max_value}", value=value
                    )
        return None


@dataclass
class Identifiable:
    """Base class for models with ID."""

    id: str

    def __eq__(self, other) -> bool:
        """Check equality by ID."""
        if not isinstance(other, self.__class__):
            return False
        return self.id == other.id

    def __hash__(self) -> int:
        """Hash by ID."""
        return hash(self.id)


@dataclass
class Timestamped:
    """Base class for timestamped models with timestamp management."""

    created_at: int = field(default_factory=lambda: int(time.time() * 1000))
    updated_at: int = field(default_factory=lambda: int(time.time() * 1000))

    def touch(self) -> None:
        """Update the updated_at timestamp to current time."""
        self.updated_at = int(time.time() * 1000)

    @property
    def created_datetime(self) -> datetime:
        """
        Get creation datetime as datetime object.

        Returns:
            datetime: Creation timestamp as datetime
        """
        return datetime.fromtimestamp(self.created_at / 1000)

    @property
    def updated_datetime(self) -> datetime:
        """
        Get update datetime as datetime object.

        Returns:
            datetime: Update timestamp as datetime
        """
        return datetime.fromtimestamp(self.updated_at / 1000)

    @property
    def age_milliseconds(self) -> int:
        """
        Get age in milliseconds since creation.

        Returns:
            int: Age in milliseconds
        """
        return int(time.time() * 1000) - self.created_at

    @property
    def age_seconds(self) -> float:
        """
        Get age in seconds since creation.

        Returns:
            float: Age in seconds
        """
        return self.age_milliseconds / 1000

    @property
    def age_minutes(self) -> float:
        """
        Get age in minutes since creation.

        Returns:
            float: Age in minutes
        """
        return self.age_seconds / 60

    @property
    def age_hours(self) -> float:
        """
        Get age in hours since creation.

        Returns:
            float: Age in hours
        """
        return self.age_seconds / 3600

    @property
    def age_days(self) -> float:
        """
        Get age in days since creation.

        Returns:
            float: Age in days
        """
        return self.age_seconds / 86400

    @property
    def last_update_seconds_ago(self) -> float:
        """
        Get seconds since last update.

        Returns:
            float: Seconds since last update
        """
        return (int(time.time() * 1000) - self.updated_at) / 1000

    def is_fresh(self, max_age_seconds: float) -> bool:
        """
        Check if data is fresh (age is less than max_age_seconds).

        Args:
            max_age_seconds: Maximum age in seconds

        Returns:
            bool: True if fresh (age <= max_age_seconds)
        """
        return self.age_seconds <= max_age_seconds

    def is_stale(self, max_age_seconds: float) -> bool:
        """
        Check if data is stale (age is greater than max_age_seconds).

        Args:
            max_age_seconds: Maximum age in seconds

        Returns:
            bool: True if stale (age > max_age_seconds)
        """
        return self.age_seconds > max_age_seconds

    def is_recently_updated(self, max_seconds_ago: float) -> bool:
        """
        Check if recently updated.

        Args:
            max_seconds_ago: Maximum seconds since last update

        Returns:
            bool: True if recently updated
        """
        return self.last_update_seconds_ago <= max_seconds_ago

    def format_age(self) -> str:
        """
        Format age as human-readable string.

        Returns:
            str: Formatted age string
        """
        seconds = self.age_seconds
        if seconds < 60:
            return f"{seconds:.1f}s"
        elif seconds < 3600:
            return f"{seconds / 60:.1f}m"
        elif seconds < 86400:
            return f"{seconds / 3600:.1f}h"
        else:
            return f"{seconds / 86400:.1f}d"

    def format_created(self, fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
        """
        Format creation time as string.

        Args:
            fmt: DateTime format string

        Returns:
            str: Formatted datetime string
        """
        return self.created_datetime.strftime(fmt)

    def format_updated(self, fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
        """
        Format update time as string.

        Args:
            fmt: DateTime format string

        Returns:
            str: Formatted datetime string
        """
        return self.updated_datetime.strftime(fmt)

    def get_time_until_stale(self, max_age_seconds: float) -> float:
        """
        Get seconds remaining until data becomes stale.

        Args:
            max_age_seconds: Maximum age in seconds

        Returns:
            float: Seconds until stale (negative if already stale)
        """
        return max_age_seconds - self.age_seconds

    def set_created_at(self, timestamp: int) -> None:
        """
        Set creation timestamp manually (for deserialization).

        Args:
            timestamp: Timestamp in milliseconds
        """
        self.created_at = timestamp

    def set_updated_at(self, timestamp: int) -> None:
        """
        Set update timestamp manually (for deserialization).

        Args:
            timestamp: Timestamp in milliseconds
        """
        self.updated_at = timestamp
