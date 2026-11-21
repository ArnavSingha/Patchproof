"""Immutable models representing AST inspection and blast-radius results."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class EntityType(StrEnum):
    """Types of syntactic code structures."""

    FUNCTION = "function"
    METHOD = "method"
    CLASS = "class"
    MODULE = "module"


class ASTEntity(BaseModel):
    """Represents a specific syntactic code structure located within a source file."""

    model_config = ConfigDict(frozen=True)

    name: str = Field(description="Identifier name of the function, method, or class")
    entity_type: EntityType = Field(description="Category of code entity")
    parent_class: str | None = Field(default=None, description="Enclosing class name if a method")
    start_line: int = Field(description="1-based starting line number in source")
    end_line: int = Field(description="1-based ending line number in source")


class FileASTReport(BaseModel):
    """Syntactic report for an individual source file affected by a patch."""

    model_config = ConfigDict(frozen=True)

    path: str = Field(description="Relative path of the analyzed source file")
    modified_entities: tuple[ASTEntity, ...] = Field(
        default_factory=tuple,
        description="Functions, methods, or classes directly intersected by modified lines",
    )
    imported_modules: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Static imports discovered in this file",
    )
    syntactic_calls: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Syntactic call sites invoked within the modified line ranges",
    )


class ASTAnalysisReport(BaseModel):
    """Aggregated deterministic AST analysis across all modified files."""

    model_config = ConfigDict(frozen=True)

    file_reports: tuple[FileASTReport, ...] = Field(default_factory=tuple)

    @property
    def all_modified_entities(self) -> tuple[ASTEntity, ...]:
        """Flattened tuple of all modified entities across all files."""
        entities: list[ASTEntity] = []
        for rep in self.file_reports:
            entities.extend(rep.modified_entities)
        return tuple(entities)

    @property
    def all_imports(self) -> tuple[str, ...]:
        """Unique sorted list of all imported modules across modified files."""
        imports: set[str] = set()
        for rep in self.file_reports:
            imports.update(rep.imported_modules)
        return tuple(sorted(imports))

    @property
    def all_calls(self) -> tuple[str, ...]:
        """Unique sorted list of all syntactic calls made in modified lines."""
        calls: set[str] = set()
        for rep in self.file_reports:
            calls.update(rep.syntactic_calls)
        return tuple(sorted(calls))
