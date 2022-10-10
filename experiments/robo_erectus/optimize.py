#!/usr/bin/env python3
"""Setup and running of the optimize modular program."""

import logging
import os
from random import Random

import multineat
from genotype import random as random_genotype
from optimizer import Optimizer
from revolve2.core.database import open_async_database_sqlite
from revolve2.core.optimization import ProcessIdGen

EXPERIMENT_NAME = "default"
DATABASE_DIR = os.path.join('./database', EXPERIMENT_NAME)
ANALYSIS_DIR = os.path.join(DATABASE_DIR, "analysis/")

def ensure_dirs():
    if not os.path.isdir(ANALYSIS_DIR):
        os.mkdir(ANALYSIS_DIR)

async def main() -> None:
    """Run the optimization process."""
    RNG_SEED = 420

    # number of initial mutations for body and brain CPPNWIN networks
    NUM_INITIAL_MUTATIONS = 10

    SIMULATION_TIME = 30
    SAMPLING_FREQUENCY = 10
    CONTROL_FREQUENCY = 10

    POPULATION_SIZE = 10
    OFFSPRING_SIZE = 10
    NUM_GENERATIONS = 50

    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] [%(levelname)s] [%(module)s] %(message)s",
    )

    logging.info("Starting optimization")

    # random number generator
    rng = Random()
    rng.seed(RNG_SEED)

    # database
    database = open_async_database_sqlite(f"./database/{EXPERIMENT_NAME}")

    # process id generator
    process_id_gen = ProcessIdGen()
    process_id = process_id_gen.gen()

    # multineat innovation databases
    innov_db_body = multineat.InnovationDatabase()
    innov_db_brain = multineat.InnovationDatabase()

    initial_population = [
        random_genotype(innov_db_body, innov_db_brain, rng, NUM_INITIAL_MUTATIONS)
        for _ in range(POPULATION_SIZE)
    ]

    maybe_optimizer = await Optimizer.from_database(
        database=database,
        process_id=process_id,
        innov_db_body=innov_db_body,
        innov_db_brain=innov_db_brain,
        rng=rng,
        process_id_gen=process_id_gen,
    )
    if maybe_optimizer is not None:
        print("initilized with existing database ")
        optimizer = maybe_optimizer
    else:
        print("initialized from scratch...")
        optimizer = await Optimizer.new(
            database=database,
            process_id=process_id,
            initial_population=initial_population,
            rng=rng,
            process_id_gen=process_id_gen,
            innov_db_body=innov_db_body,
            innov_db_brain=innov_db_brain,
            simulation_time=SIMULATION_TIME,
            sampling_frequency=SAMPLING_FREQUENCY,
            control_frequency=CONTROL_FREQUENCY,
            num_generations=NUM_GENERATIONS,
            offspring_size=OFFSPRING_SIZE,
        )

    logging.info("Starting optimization process..")

    await optimizer.run()

    logging.info("Finished optimizing.")


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())