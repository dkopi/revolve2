"""
Fixed morphology creator
refer to ci-group/revolve/experiments/examples/yaml & revolve/pyrevolve/revolve_bot/revolve_bot.py 
"""
import os
import yaml
import math
from revolve2.core.modular_robot import ActiveHinge, Body, Core, Brick

SCRIPT_DIR = os.path.abspath(os.path.dirname(__file__))


class FixedBodyCreator:
    def __init__(self, yaml_file):
        self.yaml_file = yaml_file
        self.load_file(yaml_file)

    def load_file(self, path, conf_type="yaml"):
        """
        Read robot's description from a file and parse it to Python structure
        :param path: Robot's description file path
        :param conf_type: Type of a robot's description format
        :return:
        """
        with open(path, "r") as robot_file:
            text = robot_file.read()

        if "yaml" == conf_type:
            self.load_yaml(text)
        elif "sdf" == conf_type:
            raise NotImplementedError("Loading from SDF not yet implemented")

    def load_yaml(self, text):
        """
        Load robot's description from a yaml string
        :param text: Robot's yaml description
        """
        yaml_bot = yaml.safe_load(text)
        self._id = yaml_bot["id"] if "id" in yaml_bot else None
        self._core = self.FromYaml(yaml_bot["body"])
        self._body = Body()
        self._body.core = self._core
        self._body.finalize()

    def FromYaml(self, yaml_object):
        """
        From a yaml object, creates a data struture of interconnected body modules.
        Standard names for modules are:
        Core
        ActiveHinge
        Brick
        """
        mod_type = yaml_object["type"]
        if mod_type == "CoreComponent" or mod_type == "Core":
            module = Core(0.0)
        elif mod_type == "ActiveHinge":
            module = ActiveHinge(math.pi / 2.0)
        elif mod_type == "Brick":
            module = Brick(0.0)
        else:
            raise NotImplementedError(
                '"{}" module not yet implemented'.format(mod_type)
            )

        # module.id = yaml_object['id']

        try:
            module.orientation = yaml_object["orientation"]
        except KeyError:
            module.orientation = 0

        try:
            module.rgb = (
                yaml_object["params"]["red"],
                yaml_object["params"]["green"],
                yaml_object["params"]["blue"],
            )
        except KeyError:
            pass

        if "children" in yaml_object:
            for parent_slot in yaml_object["children"]:
                module.children[parent_slot] = self.FromYaml(
                    yaml_object=yaml_object["children"][parent_slot]
                )

        return module

    @property
    def body(self):
        return self._body


if __name__ == "__main__":
    path = os.path.join(SCRIPT_DIR, "spider.yaml")
    erectus = FixedBodyCreator(path)
    body = erectus.body
