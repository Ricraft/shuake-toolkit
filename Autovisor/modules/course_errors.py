# encoding=utf-8
"""Shared course navigation errors without browser or utility dependencies."""


class CourseNavigationError(RuntimeError):
    """A course-specific navigation failure that may not affect later courses."""


class CourseAuthenticationError(CourseNavigationError):
    """The course navigation lost authentication and the whole queue must stop."""
