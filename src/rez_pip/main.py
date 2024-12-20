import os
import sys
import typing
import logging
import pathlib

if sys.version_info >= (3, 10):
    import importlib.metadata as importlib_metadata
else:
    import importlib_metadata

import rich
import rez.package_maker
import rez.version
import rich.markup

import rez_pip.pip
import rez_pip.rez
import rez_pip.install
import rez_pip.download
import rez_pip.exceptions

_LOG = logging.getLogger(__name__)


def run_installation_for_python(
    pipPackageNames: typing.List[str],
    pythonExecutable: pathlib.Path,
    pythonVersion: str,
    pipPath: pathlib.Path,
    pipWorkArea: pathlib.Path,
    pipArgs: typing.Optional[typing.List[str]] = None,
    requirementPath: typing.Optional[typing.List[str]] = None,
    constraintPath: typing.Optional[typing.List[str]] = None,
    rezInstallPath: typing.Optional[str] = None,
    rezRelease: bool = False,
    rezPackageCreationCallback: typing.Optional[
        typing.Callable[
            [
                rez.package_maker.PackageMaker,
                importlib_metadata.Distribution,
                rez.version.Version,
            ],
            None,
        ]
    ] = None,
) -> typing.List[rez.package_maker.PackageMaker]:
    """
    Convert and install the given pip packages to rez packages using the given python version.

    :param pipPackageNames: list of packages to install, in the syntax understood by pip.
    :param pythonExecutable: filesystem path to an existing python interpreter
    :param pythonVersion: a full rez version of the python package provided as executable
    :param pipPath: filesystem path to the pip executable. If not provided use the bundled pip.
    :param requirementPath: optional filesystem path to an existing python requirement file.
    :param constraintPath: optional filesystem path to an existing python constraint file.
    :param rezInstallPath:
        optional filesystem path to an existing directory where to install the packages.
        Default is the "local_packages_path".
    :param rezRelease: True to release the package to the "release_packages_path"
    :param pipArgs: additional argument passed directly to pip
    :param pipWorkArea:
        filesystem path to an existing directory that can be used for pip to install packages.
    :param rezPackageCreationCallback:
        a function that is called for each rez package created, signature is as follows:
        ``callable("package being created", "pip distribution", "python version")``.
        It is being called after the package has been configured by rez_pip.
    :return:
        dict of rez packages created per python version: ``{"pythonVersion": PackageMaker()}``
        Note the PackageMaker object are already "close" and written to disk.
    """
    wheelsDir = pipWorkArea / "wheels"
    os.makedirs(wheelsDir, exist_ok=True)

    # Suffix with the python version because we loop over multiple versions,
    # and package versions, content, etc can differ for each Python version.
    installedWheelsDir = pipWorkArea / "installed" / pythonVersion
    os.makedirs(installedWheelsDir, exist_ok=True)

    with rich.get_console().status(
        f"[bold]Resolving dependencies for {rich.markup.escape(', '.join(pipPackageNames))} (python-{pythonVersion})"
    ):
        pipPackages = rez_pip.pip.getPackages(
            pipPackageNames,
            str(pipPath),
            pythonVersion,
            str(pythonExecutable),
            requirementPath or [],
            constraintPath or [],
            pipArgs or [],
        )

    _LOG.info(f"Resolved {len(pipPackages)} dependencies for python {pythonVersion}")

    # TODO: Should we postpone downloading to the last minute if we can?
    _LOG.info("[bold]Downloading...")
    wheels = rez_pip.download.downloadPackages(pipPackages, str(wheelsDir))
    _LOG.info(f"[bold]Downloaded {len(wheels)} wheels")

    dists: typing.Dict[importlib_metadata.Distribution, bool] = {}

    with rich.get_console().status(
        f"[bold]Installing wheels into {installedWheelsDir!r}"
    ):
        for package, wheel in zip(pipPackages, wheels):
            _LOG.info(f"[bold]Installing {package.name}-{package.version} wheel")
            dist, isPure = rez_pip.install.installWheel(
                package, pathlib.Path(wheel), str(installedWheelsDir)
            )

            dists[dist] = isPure

    distNames = [dist.name for dist in dists.keys()]

    rezPackages = []

    with rich.get_console().status("[bold]Creating rez packages..."):
        for dist, package in zip(dists, pipPackages):
            isPure = dists[dist]
            rezPackage = rez_pip.rez.createPackage(
                dist,
                isPure,
                rez.version.Version(pythonVersion),
                distNames,
                str(installedWheelsDir),
                wheelURL=package.download_info.url,
                prefix=rezInstallPath,
                release=rezRelease,
                creationCallback=rezPackageCreationCallback,
            )
            rezPackages.append(rezPackage)

    return rezPackages


def run_full_installation(
    pipPackageNames: typing.List[str],
    pythonVersionRange: typing.Optional[str],
    pipPath: pathlib.Path,
    pipWorkArea: pathlib.Path,
    pipArgs: typing.Optional[typing.List[str]] = None,
    requirementPath: typing.Optional[typing.List[str]] = None,
    constraintPath: typing.Optional[typing.List[str]] = None,
    rezInstallPath: typing.Optional[str] = None,
    rezRelease: bool = False,
    rezPackageCreationCallback: typing.Optional[
        typing.Callable[
            [
                rez.package_maker.PackageMaker,
                importlib_metadata.Distribution,
                rez.version.Version,
            ],
            None,
        ]
    ] = None,
) -> typing.Dict[str, typing.List[rez.package_maker.PackageMaker]]:
    """
    Convert and install the given pip packages to rez packages using the given python versions.

    :param pipPackageNames: list of packages to install, in the syntax understood by pip.
    :param pythonVersionRange: a single or range of python versions in the rez syntax.
    :param pipPath: filesystem path to the pip executable. If not provided use the bundled pip.
    :param requirementPath: optional filesystem path to an existing python requirement file.
    :param constraintPath: optional filesystem path to an existing python constraint file.
    :param rezInstallPath:
        optional filesystem path to an existing directory where to install the packages.
        Default is the "local_packages_path".
    :param rezRelease: True to release the package to the "release_packages_path"
    :param pipArgs: additional argument passed directly to pip
    :param pipWorkArea:
        filesystem path to an existing directory that can be used for pip to install packages.
    :param rezPackageCreationCallback:
        a function that is called for each rez package created, signature is as follows:
        ``callable("package being created", "pip distribution", "python version")``.
        It is being called after the package has been configured by rez_pip.
    :return:
        dict of rez packages created per python version: ``{"pythonVersion": PackageMaker()}``
        Note the PackageMaker object are already "close" and written to disk.
    """
    pythonVersions = rez_pip.rez.getPythonExecutables(
        pythonVersionRange, packageFamily="python"
    )

    if not pythonVersions:
        raise rez_pip.exceptions.RezPipError(
            f'No "python" package found within the range {pythonVersionRange!r}.'
        )

    rezPackages: typing.Dict[str, typing.List[rez.package_maker.PackageMaker]] = {}

    for pythonVersion, pythonExecutable in pythonVersions.items():
        _LOG.info(
            f"[bold underline]Installing requested packages for Python {pythonVersion}"
        )
        packages = run_installation_for_python(
            pipPackageNames=pipPackageNames,
            pythonExecutable=pythonExecutable,
            pythonVersion=pythonVersion,
            pipPath=pipPath,
            pipWorkArea=pipWorkArea,
            pipArgs=pipArgs,
            requirementPath=requirementPath,
            constraintPath=constraintPath,
            rezInstallPath=rezInstallPath,
            rezRelease=rezRelease,
            rezPackageCreationCallback=rezPackageCreationCallback,
        )
        rezPackages[pythonVersion] = packages

    return rezPackages
