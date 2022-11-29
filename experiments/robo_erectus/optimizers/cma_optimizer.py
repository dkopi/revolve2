"""Optimizer for finding a good modular robot body and brain using Linear genotypes and simulation using mujoco."""
import os
import sys

sys.path.append(os.getcwd())
import logging
import pickle
from random import Random
from typing import List, Tuple

import multineat
import numpy as np
import sqlalchemy
import wandb

# from experiments.robo_erectus.fitness import fitness_functions

from fitness import fitness_functions
from measures import *
from revolve2.core.physics.running._results import ActorState
from revolve2.actor_controller import ActorController
from revolve2.core.database import IncompatibleError
from revolve2.core.database.serializers import FloatSerializer
from revolve2.core.optimization import ProcessIdGen
from ._cma_optimizer import EsOptimizer
from revolve2.core.physics.actor import Actor
from revolve2.core.physics.running import (
    Batch,
    Environment,
    PosedActor,
)
from revolve2.runners.mujoco import LocalRunner
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.ext.asyncio.session import AsyncSession
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.future import select
from joblib import Parallel, delayed
from controllers.controller_wrapper import *

from genotypes.linear_controller_genotype import (
    LinearControllerGenotype,
    LinearGenotypeSerializer,
)

import wandb
from fitness import fitness_functions
from measures import *
import utilities
from utilities import (
    actor_get_default_pose,
    actor_get_standing_pose,
)


class CmaEsOptimizer(EsOptimizer[LinearControllerGenotype, float]):
    """
    Optimizer for the problem.

    Uses the generic EA optimizer as a base.
    """

    _process_id: int

    _controllers: List[ActorController]

    _rng: Random

    _simulation_time: int
    _sampling_frequency: float
    _control_frequency: float

    _num_generations: int

    _fitness_function: str

    _headless: bool = True  # whether to hide sim GUI

    n_jobs: int = 1

    _body_yaml: str

    _Genotype = LinearControllerGenotype

    async def ainit_new(  # type: ignore # TODO for now ignoring mypy complaint about LSP problem, override parent's ainit
        self,
        database: AsyncEngine,
        session: AsyncSession,
        process_id: int,
        process_id_gen: ProcessIdGen,
        initial_population: List[LinearControllerGenotype],
        rng: Random,
        innov_db_body: None,
        innov_db_brain: None,
        simulation_time: int,
        sampling_frequency: float,
        control_frequency: float,
        num_generations: int,
        offspring_size: int,
        fitness_function: str,
        body_name: str,
        headless: bool = True,
    ) -> None:
        """
        Initialize this class async.

        Called when creating an instance using `new`.

        :param database: Database to use for this optimizer.
        :param session: Session to use when saving data to the database during initialization.
        :param process_id: Unique identifier in the completely program specifically made for this optimizer.
        :param process_id_gen: Can be used to create more unique identifiers.
        :param initial_population: List of genotypes forming generation 0.
        :param rng: Random number generator.
        :param innov_db_body: Innovation database for the body genotypes.
        :param innov_db_brain: Innovation database for the brain genotypes.
        :param simulation_time: Time in second to simulate the robots for.
        :param sampling_frequency: Sampling frequency for the simulation. See `Batch` class from physics running.
        :param control_frequency: Control frequency for the simulation. See `Batch` class from physics running.
        :param num_generations: Number of generation to run the optimizer for.
        :param offspring_size: Number of offspring made by the population each generation.
        """
        await super().ainit_new(
            database=database,
            session=session,
            process_id=process_id,
            process_id_gen=process_id_gen,
            genotype_type=LinearControllerGenotype,
            genotype_serializer=LinearGenotypeSerializer,
            fitness_type=float,
            fitness_serializer=FloatSerializer,
            offspring_size=offspring_size,
            initial_population=initial_population,
        )

        self._process_id = process_id
        self._headless = headless
        self._rng = rng
        self._simulation_time = simulation_time
        self._sampling_frequency = sampling_frequency
        self._control_frequency = control_frequency
        self._num_generations = num_generations
        self._fitness_function = fitness_function
        self._body_name = body_name

        # create database structure if not exists
        # TODO this works but there is probably a better way
        await (await session.connection()).run_sync(DbBase.metadata.create_all)

        # save to database
        self._on_generation_checkpoint(session)

    async def ainit_from_database(  # type: ignore # see comment at ainit_new
        self,
        database: AsyncEngine,
        session: AsyncSession,
        process_id: int,
        process_id_gen: ProcessIdGen,
        rng: Random,
        innov_db_body: multineat.InnovationDatabase,
        innov_db_brain: multineat.InnovationDatabase,
        headless: bool = True,
    ) -> bool:
        """
        Try to initialize this class async from a database.

        Called when creating an instance using `from_database`.

        :param database: Database to use for this optimizer.
        :param session: Session to use when loading and saving data to the database during initialization.
        :param process_id: Unique identifier in the completely program specifically made for this optimizer.
        :param process_id_gen: Can be used to create more unique identifiers.
        :param rng: Random number generator.
        :param innov_db_body: Innovation database for the body genotypes.
        :param innov_db_brain: Innovation database for the brain genotypes.
        :returns: True if this complete object could be deserialized from the database.
        :raises IncompatibleError: In case the database is not compatible with this class.
        """
        if not await super().ainit_from_database(
            database=database,
            session=session,
            process_id=process_id,
            process_id_gen=process_id_gen,
            genotype_type=LinearControllerGenotype,
            genotype_serializer=LinearGenotypeSerializer,
            fitness_type=float,
            fitness_serializer=FloatSerializer,
        ):
            return False

        self._process_id = process_id
        self._headless = headless

        opt_row = (
            (
                await session.execute(
                    select(DbOptimizerState)
                    .filter(DbOptimizerState.process_id == process_id)
                    .order_by(DbOptimizerState.generation_index.desc())
                )
            )
            .scalars()
            .first()
        )

        # if this happens something is wrong with the database
        if opt_row is None:
            raise IncompatibleError

        self._simulation_time = opt_row.simulation_time
        self._sampling_frequency = opt_row.sampling_frequency
        self._control_frequency = opt_row.control_frequency
        self._num_generations = opt_row.num_generations

        self._rng = rng
        self._rng.setstate(pickle.loads(opt_row.rng))

        self._fitness_function = opt_row.fitness_function
        self._body_name = opt_row.body_name

        return True

    def _must_do_next_gen(self) -> bool:
        return self.generation_index != self._num_generations

    async def _evaluate_generation(
        self,
        genotypes: List[LinearControllerGenotype],
    ) -> List[float]:
        _simulation_time = self._simulation_time
        _sampling_frequency = self._sampling_frequency
        _control_frequency = self._control_frequency

        def _evaluate(genotype, headless):
            actor, controller = genotype.develop()

            controller_wrapper = ControllerWrapper(controller)
            batch = Batch(
                simulation_time=_simulation_time,
                sampling_frequency=_sampling_frequency,
                control_frequency=_control_frequency,
                control=controller_wrapper._control,
            )

            pos, rot = actor_get_standing_pose(actor)
            # pos, rot = actor_get_default_pose(actor)
            env = Environment()
            env.actors.append(
                PosedActor(
                    actor,
                    pos,
                    rot,
                    [0.0 for _ in controller.get_dof_targets()],
                ),
            )
            batch.environments.append(env)

            is_healthy = genotypes[0].is_healthy

            return LocalRunner(headless=headless).run_batch_sync(
                batch, is_healthy=is_healthy
            )

        logging.info(
            f"Starting simulation batch with mujoco - {len(genotypes)} evaluations."
        )
        if self.n_jobs > 1:
            batch_results = Parallel(n_jobs=self.n_jobs)(
                delayed(_evaluate)(genotype, True) for genotype in genotypes
            )
        else:
            batch_results = [
                _evaluate(genotype, self._headless) for genotype in genotypes
            ]
        logging.info("Finished batch.")

        environment_results = [br.environment_results[0] for br in batch_results]

        logging.info(self._fitness_function)
        fitness = [
            fitness_functions[self._fitness_function](environment_result)
            for environment_result in environment_results
        ]

        return fitness, environment_results

    def _log_results(self) -> None:
        displacement = [displacement_measure(r) for r in self._latest_results]

        wandb.log(
            {
                "displacement_max": max(displacement),
                "displacement_avg": sum(displacement) / len(displacement),
                "displacement_min": min(displacement),
                "fitness_max": max(self._latest_fitnesses),
                "fitness_avg": sum(self._latest_fitnesses)
                / len(self._latest_fitnesses),
                "fitness_min": min(self._latest_fitnesses),
                "displacement": wandb.Histogram(displacement),
                "max_height_relative_to_avg_height": wandb.Histogram(
                    [
                        max_height_relative_to_avg_height_measure(r)
                        for r in self._latest_results
                    ]
                ),
                "ground_contact_measure": wandb.Histogram(
                    [ground_contact_measure(r) for r in self._latest_results]
                ),
            }
        )

    def _on_generation_checkpoint(self, session: AsyncSession) -> None:
        session.add(
            DbOptimizerState(
                process_id=self._process_id,
                generation_index=self.generation_index,
                rng=pickle.dumps(self._rng.getstate()),
                simulation_time=self._simulation_time,
                sampling_frequency=self._sampling_frequency,
                control_frequency=self._control_frequency,
                num_generations=self._num_generations,
                fitness_function=self._fitness_function,
                body_name=self._body_name,
            )
        )


DbBase = declarative_base()


class DbOptimizerState(DbBase):
    """Optimizer state."""

    __tablename__ = "optimizer"

    process_id = sqlalchemy.Column(
        sqlalchemy.Integer,
        nullable=False,
        primary_key=True,
    )
    generation_index = sqlalchemy.Column(
        sqlalchemy.Integer, nullable=False, primary_key=True
    )
    rng = sqlalchemy.Column(sqlalchemy.PickleType, nullable=False)
    # innov_db_body = sqlalchemy.Column(sqlalchemy.String, nullable=False)
    # innov_db_brain = sqlalchemy.Column(sqlalchemy.String, nullable=False)
    simulation_time = sqlalchemy.Column(sqlalchemy.Integer, nullable=False)
    sampling_frequency = sqlalchemy.Column(sqlalchemy.Float, nullable=False)
    control_frequency = sqlalchemy.Column(sqlalchemy.Float, nullable=False)
    num_generations = sqlalchemy.Column(sqlalchemy.Integer, nullable=False)
    fitness_function = sqlalchemy.Column(sqlalchemy.String, nullable=False)

    body_name = sqlalchemy.Column(
        sqlalchemy.String, nullable=False
    )  # e.g. "erectus" | "spider"