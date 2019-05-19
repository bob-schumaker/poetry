from __future__ import absolute_import
from __future__ import unicode_literals

from .__version__ import __version__
from .config.config import Config
from .packages import Locker
from .packages import ProjectPackage
from .repositories import Pool
from .repositories.auth import Auth
from .repositories.legacy_repository import LegacyRepository
from .repositories.pypi_repository import PyPiRepository
from .semver import parse_single_constraint
from .semver.version import Version
from .spdx import license_by_id
from .utils.env import Env
from .utils._compat import Path
from .utils.toml_file import TomlFile


class Poetry:

    VERSION = __version__

    def __init__(
        self,
        file,  # type: Path
        local_config,  # type: dict
        package,  # type: ProjectPackage
        locker,  # type: Locker
        config,  # type: Config
    ):
        self._file = TomlFile(file)
        self._package = package
        self._local_config = local_config
        self._locker = locker
        self._config = config
        self._pool = Pool()

    @property
    def file(self):
        return self._file

    @property
    def package(self):  # type: () -> ProjectPackage
        return self._package

    @property
    def local_config(self):  # type: () -> dict
        return self._local_config

    @property
    def locker(self):  # type: () -> Locker
        return self._locker

    @property
    def pool(self):  # type: () -> Pool
        return self._pool

    @property
    def config(self):  # type: () -> Config
        return self._config

    @classmethod
    def create(cls, cwd):  # type: (Path) -> Poetry
        poetry_file = cls.locate(cwd)

        local_config = TomlFile(poetry_file.as_posix()).read()
        if "tool" not in local_config or "poetry" not in local_config["tool"]:
            raise RuntimeError(
                "[tool.poetry] section not found in {}".format(poetry_file.name)
            )
        local_config = local_config["tool"]["poetry"]

        # Checking validity
        check_result = cls.check(local_config)
        if check_result["errors"]:
            message = ""
            for error in check_result["errors"]:
                message += "  - {}\n".format(error)

            raise RuntimeError("The Poetry configuration is invalid:\n" + message)

        # Load package
        name = local_config["name"]
        version = local_config["version"]
        package = ProjectPackage(name, version, version)
        package.root_dir = poetry_file.parent

        for author in local_config["authors"]:
            package.authors.append(author)

        for maintainer in local_config.get("maintainers", []):
            package.maintainers.append(maintainer)

        package.description = local_config.get("description", "")
        package.homepage = local_config.get("homepage")
        package.repository_url = local_config.get("repository")
        package.documentation_url = local_config.get("documentation")
        try:
            license_ = license_by_id(local_config.get("license", ""))
        except ValueError:
            license_ = None

        package.license = license_
        package.keywords = local_config.get("keywords", [])
        package.classifiers = local_config.get("classifiers", [])

        if "readme" in local_config:
            package.readme = Path(poetry_file.parent) / local_config["readme"]

        if "platform" in local_config:
            package.platform = local_config["platform"]

        if "dependencies" in local_config:
            for name, constraint in local_config["dependencies"].items():
                if name.lower() == "python":
                    package.python_versions = constraint
                    continue

                if isinstance(constraint, list):
                    for _constraint in constraint:
                        package.add_dependency(name, _constraint)

                    continue

                package.add_dependency(name, constraint)

        if "dev-dependencies" in local_config:
            env = Env.get(cwd).get_marker_env()
            env_python_version = Version.parse(env['python_version'])
            for name, constraint in local_config["dev-dependencies"].items():
                if not isinstance(constraint, list):
                    constraint = [constraint]
                for _constraint in constraint:
                    if 'python' in _constraint:
                        python_constraint = parse_single_constraint(_constraint['python'])
                        if not python_constraint.allows(env_python_version):
                            continue
                    package.add_dependency(name, _constraint, category="dev")

        extras = local_config.get("extras", {})
        for extra_name, requirements in extras.items():
            package.extras[extra_name] = []

            # Checking for dependency
            for req in requirements:
                req = Dependency(req, "*")

                for dep in package.requires:
                    if dep.name == req.name:
                        dep.in_extras.append(extra_name)
                        package.extras[extra_name].append(dep)

                        break

        if "build" in local_config:
            package.build = local_config["build"]

        if "include" in local_config:
            package.include = local_config["include"]

        if "exclude" in local_config:
            package.exclude = local_config["exclude"]

        if "packages" in local_config:
            package.packages = local_config["packages"]

        # Custom urls
        if "urls" in local_config:
            package.custom_urls = local_config["urls"]

        # Moving lock if necessary (pyproject.lock -> poetry.lock)
        lock = poetry_file.parent / "poetry.lock"
        if not lock.exists():
            # Checking for pyproject.lock
            old_lock = poetry_file.with_suffix(".lock")
            if old_lock.exists():
                shutil.move(str(old_lock), str(lock))

        locker = Locker(poetry_file.parent / "poetry.lock", local_config)

        config = Config()
        # Load global config
        config_file = TomlFile(Path(CONFIG_DIR) / "config.toml")
        if config_file.exists():
            config.merge(config_file.read())

        local_config_file = TomlFile(poetry_file.parent / "poetry.toml")
        if local_config_file.exists():
            config.merge(local_config_file.read())

        # Load global auth config
        auth_config_file = TomlFile(Path(CONFIG_DIR) / "auth.toml")
        if auth_config_file.exists():
            config.merge(auth_config_file.read())

        return cls(poetry_file, local_config, package, locker, config)

    def create_legacy_repository(
        self, source
    ):  # type: (Dict[str, str]) -> LegacyRepository
        if "url" in source:
            # PyPI-like repository
            if "name" not in source:
                raise RuntimeError("Missing [name] in source.")
        else:
            raise RuntimeError("Unsupported source specified")

        name = source["name"]
        url = source["url"]
        credentials = get_http_basic_auth(self._config, name)
        if not credentials:
            return LegacyRepository(name, url)

        auth = Auth(url, credentials[0], credentials[1])

        return LegacyRepository(name, url, auth=auth)

    @classmethod
    def locate(cls, cwd):  # type: (Path) -> Poetry
        candidates = [Path(cwd)]
        candidates.extend(Path(cwd).parents)

        for path in candidates:
            poetry_file = path / "pyproject.toml"

            if poetry_file.exists():
                return poetry_file

        else:
            raise RuntimeError(
                "Poetry could not find a pyproject.toml file in {} or its parents".format(
                    cwd
                )
            )

    @classmethod
    def check(cls, config, strict=False):  # type: (dict, bool) -> Dict[str, List[str]]
        """
        Checks the validity of a configuration
        """
        result = {"errors": [], "warnings": []}
        # Schema validation errors
        validation_errors = validate_object(config, "poetry-schema")

        result["errors"] += validation_errors

        if strict:
            # If strict, check the file more thoroughly

        return self

    def set_pool(self, pool):  # type: (Pool) -> Poetry
        self._pool = pool

        return self

    def set_config(self, config):  # type: (Config) -> Poetry
        self._config = config

        return self
