#!/usr/bin/env python3
# Copyright (c) Facebook, Inc. and its affiliates.
#
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

# pyre-strict

from __future__ import annotations

from dataclasses import dataclass, field
from functools import reduce
from logging import Logger
from typing import Dict, List, Optional, Tuple, Union, Set

from ax.core.arm import Arm
from ax.core.parameter import FixedParameter, Parameter, RangeParameter
from ax.core.parameter_constraint import (
    OrderConstraint,
    ParameterConstraint,
    SumConstraint,
)
from ax.core.types import TParameterization
from ax.exceptions.core import UserInputError
from ax.utils.common.base import Base
from ax.utils.common.logger import get_logger
from ax.utils.common.typeutils import not_none


logger: Logger = get_logger(__name__)


class SearchSpace(Base):
    """Base object for SearchSpace object.

    Contains a set of Parameter objects, each of which have a
    name, type, and set of valid values. The search space also contains
    a set of ParameterConstraint objects, which can be used to define
    restrictions across parameters (e.g. p_a < p_b).
    """

    def __init__(
        self,
        parameters: List[Parameter],
        parameter_constraints: Optional[List[ParameterConstraint]] = None,
    ) -> None:
        """Initialize SearchSpace

        Args:
            parameters: List of parameter objects for the search space.
            parameter_constraints: List of parameter constraints.
        """
        if len({p.name for p in parameters}) < len(parameters):
            raise ValueError("Parameter names must be unique.")

        self._parameters: Dict[str, Parameter] = {p.name: p for p in parameters}
        self.set_parameter_constraints(parameter_constraints or [])

    @property
    def parameters(self) -> Dict[str, Parameter]:
        return self._parameters

    @property
    def parameter_constraints(self) -> List[ParameterConstraint]:
        return self._parameter_constraints

    @property
    def range_parameters(self) -> Dict[str, Parameter]:
        return {
            name: parameter
            for name, parameter in self._parameters.items()
            if isinstance(parameter, RangeParameter)
        }

    @property
    def tunable_parameters(self) -> Dict[str, Parameter]:
        return {
            name: parameter
            for name, parameter in self._parameters.items()
            if not isinstance(parameter, FixedParameter)
        }

    def __getitem__(self, parameter_name: str) -> Parameter:
        """Retrieves the parameter"""
        if parameter_name in self.parameters:
            return self.parameters[parameter_name]
        raise ValueError(
            f"Parameter '{parameter_name}' is not part of the search space."
        )

    def add_parameter_constraints(
        self, parameter_constraints: List[ParameterConstraint]
    ) -> None:
        self._validate_parameter_constraints(parameter_constraints)
        self._parameter_constraints.extend(parameter_constraints)

    def set_parameter_constraints(
        self, parameter_constraints: List[ParameterConstraint]
    ) -> None:
        # Validate that all parameters in constraints are in search
        # space already.
        self._validate_parameter_constraints(parameter_constraints)
        # Set the parameter on the constraint to be the parameter by
        # the matching name among the search space's parameters, so we
        # are not keeping two copies of the same parameter.
        for constraint in parameter_constraints:
            if isinstance(constraint, OrderConstraint):
                constraint._lower_parameter = self._parameters[
                    constraint._lower_parameter.name
                ]
                constraint._upper_parameter = self._parameters[
                    constraint._upper_parameter.name
                ]
            elif isinstance(constraint, SumConstraint):
                for idx, parameter in enumerate(constraint.parameters):
                    constraint.parameters[idx] = self._parameters[parameter.name]

        self._parameter_constraints: List[ParameterConstraint] = parameter_constraints

    def add_parameter(self, parameter: Parameter) -> None:
        if parameter.name in self._parameters.keys():
            raise ValueError(
                f"Parameter `{parameter.name}` already exists in search space. "
                "Use `update_parameter` to update an existing parameter."
            )
        self._parameters[parameter.name] = parameter

    def update_parameter(self, parameter: Parameter) -> None:
        if parameter.name not in self._parameters.keys():
            raise ValueError(
                f"Parameter `{parameter.name}` does not exist in search space. "
                "Use `add_parameter` to add a new parameter."
            )

        prev_type = self._parameters[parameter.name].parameter_type
        if parameter.parameter_type != prev_type:
            raise ValueError(
                f"Parameter `{parameter.name}` has type {prev_type.name}. "
                f"Cannot update to type {parameter.parameter_type.name}."
            )

        self._parameters[parameter.name] = parameter

    def check_membership(
        self, parameterization: TParameterization, raise_error: bool = False
    ) -> bool:
        """Whether the given parameterization belongs in the search space.

        Checks that the given parameter values have the same name/type as
        search space parameters, are contained in the search space domain,
        and satisfy the parameter constraints.

        Args:
            parameterization: Dict from parameter name to value to validate.
            raise_error: If true parameterization does not belong, raises an error
                with detailed explanation of why.

        Returns:
            Whether the parameterization is contained in the search space.
        """
        if len(parameterization) != len(self._parameters):
            if raise_error:
                raise ValueError(
                    f"Parameterization has {len(parameterization)} parameters "
                    f"but search space has {len(self._parameters)}."
                )
            return False

        for name, value in parameterization.items():
            if name not in self._parameters:
                if raise_error:
                    raise ValueError(
                        f"Parameter {name} not defined in search space"
                        f"with parameters {self._parameters}"
                    )
                return False

            if not self._parameters[name].validate(value):
                if raise_error:
                    raise ValueError(
                        f"{value} is not a valid value for "
                        f"parameter {self._parameters[name]}"
                    )
                return False

        # parameter constraints only accept numeric parameters
        numerical_param_dict = {
            # pyre-fixme[6]: Expected `typing.Union[...oat]` but got `unknown`.
            name: float(value)
            for name, value in parameterization.items()
            if self._parameters[name].is_numeric
        }

        for constraint in self._parameter_constraints:
            if not constraint.check(numerical_param_dict):
                if raise_error:
                    raise ValueError(f"Parameter constraint {constraint} is violated.")
                return False

        return True

    def check_types(
        self,
        parameterization: TParameterization,
        allow_none: bool = True,
        raise_error: bool = False,
    ) -> bool:
        """Checks that the given parameterization's types match the search space.

        Checks that the names of the parameterization match those specified in
        the search space, and the given values are of the correct type.

        Args:
            parameterization: Dict from parameter name to value to validate.
            allow_none: Whether None is a valid parameter value.
            raise_error: If true and parameterization does not belong, raises an error
                with detailed explanation of why.

        Returns:
            Whether the parameterization has valid types.
        """
        if len(parameterization) != len(self._parameters):
            if raise_error:
                raise ValueError(
                    f"Parameterization has {len(parameterization)} parameters "
                    f"but search space has {len(self._parameters)}.\n"
                    f"Parameterization: {parameterization}.\n"
                    f"Search Space: {self._parameters}."
                )
            return False

        for name, value in parameterization.items():
            if name not in self._parameters:
                if raise_error:
                    raise ValueError(f"Parameter {name} not defined in search space")
                return False

            if value is None and allow_none:
                continue

            if not self._parameters[name].is_valid_type(value):
                if raise_error:
                    raise ValueError(
                        f"{value} is not a valid value for "
                        f"parameter {self._parameters[name]}"
                    )
                return False

        return True

    def cast_arm(self, arm: Arm) -> Arm:
        """Cast parameterization of given arm to the types in this SearchSpace.

        For each parameter in given arm, cast it to the proper type specified
        in this search space. Throws if there is a mismatch in parameter names. This is
        mostly useful for int/float, which user can be sloppy with when hand written.

        Args:
            arm: Arm to cast.

        Returns:
            New casted arm.
        """
        new_parameters: TParameterization = {}
        for name, value in arm.parameters.items():
            # Allow raw values for out of space parameters.
            if name not in self._parameters:
                new_parameters[name] = value
            else:
                new_parameters[name] = self._parameters[name].cast(value)
        return Arm(new_parameters, arm.name if arm.has_name else None)

    def out_of_design_arm(self) -> Arm:
        """Create a default out-of-design arm.

        An out of design arm contains values for some parameters which are
        outside of the search space. In the modeling conversion, these parameters
        are all stripped down to an empty dictionary, since the point is already
        outside of the modeled space.

        Returns:
            New arm w/ null parameter values.
        """
        return self.construct_arm()

    def construct_arm(
        self, parameters: Optional[TParameterization] = None, name: Optional[str] = None
    ) -> Arm:
        """Construct new arm using given parameters and name. Any
        missing parameters fallback to the experiment defaults,
        represented as None
        """
        final_parameters: TParameterization = {k: None for k in self.parameters.keys()}
        if parameters is not None:
            # Validate the param values
            for p_name, p_value in parameters.items():
                if p_name not in self.parameters:
                    raise ValueError(f"`{p_name}` does not exist in search space.")
                if p_value is not None and not self.parameters[p_name].validate(
                    p_value
                ):
                    raise ValueError(
                        f"`{p_value}` is not a valid value for parameter {p_name}."
                    )
            final_parameters.update(not_none(parameters))
        return Arm(parameters=final_parameters, name=name)

    def clone(self) -> SearchSpace:
        return SearchSpace(
            parameters=[p.clone() for p in self._parameters.values()],
            parameter_constraints=[pc.clone() for pc in self._parameter_constraints],
        )

    def _validate_parameter_constraints(
        self, parameter_constraints: List[ParameterConstraint]
    ) -> None:
        for constraint in parameter_constraints:
            if isinstance(constraint, OrderConstraint) or isinstance(
                constraint, SumConstraint
            ):
                for parameter in constraint.parameters:
                    if parameter.name not in self._parameters.keys():
                        raise ValueError(
                            f"`{parameter.name}` does not exist in search space."
                        )
                    if parameter != self._parameters[parameter.name]:
                        raise ValueError(
                            f"Parameter constraint's definition of '{parameter.name}' "
                            "does not match the SearchSpace's definition"
                        )
            else:
                for parameter_name in constraint.constraint_dict.keys():
                    if parameter_name not in self._parameters.keys():
                        raise ValueError(
                            f"`{parameter_name}` does not exist in search space."
                        )

    def __repr__(self) -> str:
        return (
            "SearchSpace("
            "parameters=" + repr(list(self._parameters.values())) + ", "
            "parameter_constraints=" + repr(self._parameter_constraints) + ")"
        )


class HierarchicalSearchSpace(SearchSpace):
    def __init__(
        self,
        parameters: List[Parameter],
        parameter_constraints: Optional[List[ParameterConstraint]] = None,
    ) -> None:
        super().__init__(
            parameters=parameters, parameter_constraints=parameter_constraints
        )
        self._all_parameter_names: Set[str] = set(self.parameters.keys())
        self._root: Parameter = self._find_root()
        self._validate_hierarchical_structure()
        logger.debug(f"Found root: {self._root}.")

    def flatten(self) -> SearchSpace:
        raise NotImplementedError  # TODO[drfreund]

    def cast_arm(self, arm: Arm) -> Arm:
        raise NotImplementedError  # TODO[drfreund]

    def _find_root(self) -> Parameter:
        """Find the root of hierarchical search space: a parameter that does not depend on
        other parameters.
        """
        dependent_parameter_names = set()
        for parameter in self.parameters.values():
            if parameter.is_hierarchical:
                for deps in parameter.dependents.values():
                    dependent_parameter_names.update(param_name for param_name in deps)

        root_parameters = self._all_parameter_names - dependent_parameter_names
        if len(root_parameters) != 1:
            num_parameters = len(self.parameters)
            # TODO: In the future, do not need to fail here; can add a "unifying" root
            # fixed parameter, on which all independent parameters in the HSS can
            # depend.
            raise NotImplementedError(
                "Could not find the root parameter; found dependent parameters "
                f"{dependent_parameter_names}, with {num_parameters} total parameters."
                f" Root parameter candidates: {root_parameters}. Having multiple "
                "independent parameters is not yet supported."
            )

        return self.parameters[root_parameters.pop()]

    def _validate_hierarchical_structure(self) -> None:
        """Validate the structure of this hierarchical search space, ensuring that all
        subtrees are independent (not sharing any parameters) and that all parameters
        are reachable and part of the tree.
        """

        def _check_subtree(root: Parameter) -> Set[str]:
            logger.debug(f"Verifying subtree with root {root}...")
            visited = {root.name}
            # Base case: validate leaf node.
            if not root.is_hierarchical:
                return visited  # TODO: Should there be other validation?

            # Recursive case: validate each subtree.
            visited_in_subtrees = (  # Generator of sets of visited parameter names.
                _check_subtree(root=self[param_name])
                for deps in root.dependents.values()
                for param_name in deps
            )
            # Check that subtrees are disjoint and return names of visited params.
            visited.update(
                reduce(
                    lambda set1, set2: _disjoint_union(set1=set1, set2=set2),
                    visited_in_subtrees,
                    next(visited_in_subtrees),
                )
            )
            logger.debug(f"Visited parameters {visited} in subtree.")
            return visited

        # Verify that all nodes have been reached.
        visited = _check_subtree(root=self._root)
        if len(self._all_parameter_names - visited) != 0:
            raise UserInputError(
                f"Parameters {self._all_parameter_names - visited} are not reachable "
                "from the root. Please check that the hierachical search space provided"
                " is represented as a valid tree with a single root."
            )
        logger.debug(f"Visited all parameters in the tree: {visited}.")


@dataclass
class SearchSpaceDigest:
    """Container for lightweight representation of search space properties.

    This is used for communicating between modelbridge and models. This is
    an ephemeral object and not meant to be stored / serialized.

    Attributes:
        feature_names: A list of parameter names.
        bounds: A list [(l_0, u_0), ..., (l_d, u_d)] of tuples representing the
            lower and upper bounds on the respective parameter (both inclusive).
        ordinal_features: A list of indices corresponding to the parameters
            to be considered as ordinal discrete parameters. The corresponding
            bounds are assumed to be integers, and parameter `i` is assumed
            to take on values `l_i, l_i+1, ..., u_i`.
        categorical_features: A list of indices corresponding to the parameters
            to be considered as categorical discrete parameters. The corresponding
            bounds are assumed to be integers, and parameter `i` is assumed
            to take on values `l_i, l_i+1, ..., u_i`.
        discrete_choices: A dictionary mapping indices of discrete (ordinal
            or categorical) parameters to their respective sets of values
            provided as a list.
        task_features: A list of parameter indices to be considered as
            task parameters.
        fidelity_features: A list of parameter indices to be considered as
            fidelity parameters.
        target_fidelities: A dictionary mapping parameter indices (of fidelity
            parameters) to their respective target fidelity value. Only used
            when generating candidates.
    """

    feature_names: List[str]
    bounds: List[Tuple[Union[int, float], Union[int, float]]]
    ordinal_features: List[int] = field(default_factory=list)
    categorical_features: List[int] = field(default_factory=list)
    discrete_choices: Dict[int, List[Union[int, float]]] = field(default_factory=dict)
    task_features: List[int] = field(default_factory=list)
    fidelity_features: List[int] = field(default_factory=list)
    target_fidelities: Dict[int, Union[int, float]] = field(default_factory=dict)


def _disjoint_union(set1: Set[str], set2: Set[str]) -> Set[str]:
    if not set1.isdisjoint(set2):
        raise UserInputError(
            "Two subtrees in the search space contain the same parameters: "
            f"{set1.intersection(set2)}."
        )
    logger.debug(f"Subtrees {set1} and {set2} are disjoint.")
    return set1.union(set2)
