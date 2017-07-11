#-*- coding: ISO-8859-1 -*-
# setup.py: the distutils script
#
# Copyright (C) 2004-2007 Gerhard Häring <gh@ghaering.de>
#
# This file is part of pysqlite.
#
# This software is provided 'as-is', without any express or implied
# warranty.  In no event will the authors be held liable for any damages
# arising from the use of this software.
#
# Permission is granted to anyone to use this software for any purpose,
# including commercial applications, and to alter it and redistribute it
# freely, subject to the following restrictions:
#
# 1. The origin of this software must not be misrepresented; you must not
#    claim that you wrote the original software. If you use this software
#    in a product, an acknowledgment in the product documentation would be
#    appreciated but is not required.
# 2. Altered source versions must be plainly marked as such, and must not be
#    misrepresented as being the original software.
# 3. This notice may not be removed or altered from any source distribution.

import glob, os, re, sys
import shutil
import urllib2
import tarfile
import tempfile
import subprocess

from distutils import log

# Building on Windows only works when setup is imported from setuptools.
# Otherwise the compiler is not correctly detected.
try:
    from setuptools import setup, Extension, Command
except ImportError:
    from distutils.core import setup, Extension, Command

from distutils.command.build import build
from distutils.command.build_ext import build_ext

import cross_bdist_wininst

# If you need to change anything, it should be enough to change setup.cfg.

sqlite = "sqlite"

PYSQLITE_EXPERIMENTAL = False

sources = ["src/module.c", "src/connection.c", "src/cursor.c", "src/cache.c",
           "src/microprotocols.c", "src/prepare_protocol.c", "src/statement.c",
           "src/util.c", "src/row.c", "src/connection_vfs.c", "src/inherit_vfs.c",
           "src/vfs.c"]

if PYSQLITE_EXPERIMENTAL:
    sources.append("src/backup.c")

include_dirs = []
library_dirs = []
libraries = []
runtime_library_dirs = []
extra_objects = []
define_macros = []

long_description = \
"""Python interface to SQLite 3

pysqlite is an interface to the SQLite 3.x embedded relational database engine.
It is almost fully compliant with the Python database API version 2.0 also
exposes the unique features of SQLite."""

def check_output(*popenargs, **kwargs):
    process = subprocess.Popen(stdout=subprocess.PIPE, *popenargs, **kwargs)
    output, unused_err = process.communicate()
    retcode = process.poll()
    if retcode:
        cmd = kwargs.get("args")
        if cmd is None:
            cmd = popenargs[0]
        raise subprocess.CalledProcessError(retcode, cmd, output=output)
    return output


if sys.platform != "win32":
    define_macros.append(('MODULE_NAME', '"pysqlite2.dbapi2"'))
else:
    define_macros.append(('MODULE_NAME', '\\"pysqlite2.dbapi2\\"'))


# On Unix platforms we can determine the install location of sqlite3 from pkg-config

if sys.platform != "win32":
    pkg_features = check_output(["pkg-config", "--variable=features", "sqlite3"])
    if not any(feature.strip() == "SQLCipher" for feature in pkg_features.split(",")):
        raise RuntimeError("pkg-config found sqlite3 but it is missing SQLCipher support")

    pkg_config_output = check_output("pkg-config --cflags --libs sqlite3", shell=True)
    for token in pkg_config_output.split():
        if token.startswith("-I"):
            include_dirs.append(token[2:])
        elif token.startswith("-l"):
            libraries.append(token[2:])
        elif token.startswith("-L"):
            library_dirs.append(token[2:])


class DocBuilder(Command):
    description = "Builds the documentation"
    user_options = []

    def initialize_options(self):
        pass

    def finalize_options(self):
        pass

    def run(self):
        try:
            shutil.rmtree("build/doc")
        except OSError:
            pass
        os.makedirs("build/doc")
        rc = os.system("sphinx-build doc/sphinx build/doc")
        if rc != 0:
            print "Is sphinx installed? If not, try 'sudo easy_install sphinx'."

AMALGAMATION_ROOT = "amalgamation"

class MyBuildExt(build_ext):
    if sys.platform == "win32":
        amalgamation = True
    else:
        amalgamation = False

    def build_extension(self, ext):
        if self.amalgamation:
            ext.define_macros.extend(configuration_defines())
            # Select OpenSSL (usually configure should autoselect this, but on
            # Windows we don't run configure)
            ext.define_macros.append(("SQLCIPHER_CRYPTO_OPENSSL", None))
            ext.sources.append(os.path.join(AMALGAMATION_ROOT, "sqlite3.c"))
            ext.include_dirs.append(AMALGAMATION_ROOT)

            # For whatever reason adding the directory to the LIB environment variable
            # did not help with building. With this hack it actually finds the libs.
            ext.library_dirs.append("C:\\openssl-win64-2010\\lib")

            # Only libcrypto is actually used I think. Maybe we could omit libssl.
            # Note that indirect dependencies have to be included as well or the
            # linker will fail.
            ext.libraries.extend(["libcrypto", "libssl", "advapi32", "crypt32", "gdi32", "user32", "ws2_32"])

        ext.define_macros.append(("THREADSAFE", "1"))
        build_ext.build_extension(self, ext)

    def __setattr__(self, k, v):
        # Make sure we don't link against the SQLite library, no matter what setup.cfg says
        if self.amalgamation and k == "libraries":
            v = None
        self.__dict__[k] = v


def configuration_defines():
    """
    Return the macros to define when building SQLite. This is used for the build
    itself and when generating the amalgamation.

    :rtype: list[tuple[str, Union[str, None]]]
    """
    return [
            # Taken from Debian/Ubuntu package 3.8.2-1
            ("SQLITE_SECURE_DELETE", None),
            ("SQLITE_ENABLE_COLUMN_METADATA", None),
            ("SQLITE_ENABLE_FTS3", None),
            ("SQLITE_ENABLE_RTREE", None),
            ("SQLITE_SOUNDEX", None),
            ("SQLITE_ENABLE_UNLOCK_NOTIFY", None),
            ("SQLITE_OMIT_LOOKASIDE", None),
            ("SQLITE_ENABLE_UPDATE_DELETE_LIMIT", None),
            # ("SQLITE_MAX_SCHEMA_RETRY", 25),  # default is 50, not sure why 25 could be better
            ("SQLITE_MAX_VARIABLE_NUMBER", "250000"),

            # Taken from https://www.sqlite.org/howtocompile.html, full-featured build
            ("SQLITE_ENABLE_FTS4", None),
            ("SQLITE_ENABLE_FTS5", None),
            ("SQLITE_ENABLE_JSON1", None),
            ("SQLITE_ENABLE_EXPLAIN_COMMENTS", None),

            # Enable SQLCipher
            ("SQLITE_HAS_CODEC", None),
    ]


class UpdateAmalgamation(Command):
    """
    Recreates the sqlite3 amalgamation in the amalgamation directory. Needs
    a unix OS to run configure and make.
    """

    description = 'recreate amalgamation from source'

    user_options = [
        ('sqlcipher-source=', None, 'URL or version of SQLCipher source download'),
        ('keep-workdir', None, 'When set, the work directory and contents are kept'),
    ]
    boolean_options = ['keep-workdir']


    def initialize_options(self):
        self.sqlcipher_source = "https://github.com/SCALE-GmbH/sqlcipher/archive/v3.3.1+scale2.tar.gz"
        self.keep_workdir = False

    def finalize_options(self):
        pass

    def run(self):
        workdir = tempfile.mkdtemp(prefix="amalg_", suffix=".tmp")
        self.announce("Processing in {0}".format(workdir), level=log.INFO)
        try:
            self._run_in(workdir)
        finally:
            if self.keep_workdir:
                self.announce("Not removing work dir {0}.".format(workdir), level=log.INFO)
            else:
                shutil.rmtree(workdir)

    def _run_in(self, workdir):
        self.announce("Downloading SQLCipher archive from {0}".format(self.sqlcipher_source), level=log.INFO)
        source_stream = urllib2.urlopen(self.sqlcipher_source)
        source_file = os.path.join(workdir, "source.tar.gz")
        with open(source_file, "wb") as archive_stream:
            shutil.copyfileobj(source_stream, archive_stream)

        self.announce("Extracting archive", level=log.INFO)
        with tarfile.open(source_file) as archive:
            rootdir, = set(
                    re.match(r".*?(?=[\/]|$)", path).group(0)
                    for path in archive.getnames())
            self.announce("Toplevel folder is {0}".format(rootdir))
            archive.extractall(workdir)

        source_dir = os.path.join(workdir, rootdir)
        self._configure_source(source_dir)
        self._make_amalgamation(source_dir)

        if not os.path.isdir(AMALGAMATION_ROOT):
            os.mkdir(AMALGAMATION_ROOT)
        for generated_file in ("sqlite3.c", "sqlite3.h", "shell.c", "sqlite3ext.h"):
            shutil.copyfile(os.path.join(source_dir, generated_file),
                            os.path.join(AMALGAMATION_ROOT, generated_file))

    def _configure_source(self, source_dir):
        self.announce("Running configure", level=log.INFO)
        def convert_define(pair):
            name, value = pair
            return "-D" + name if value is None else "-D{0}={1}".format(name, value)
        assign_cflags = "CFLAGS={0}".format(" ".join(map(convert_define, configuration_defines())))
        subprocess.check_call(["./configure", assign_cflags], cwd=source_dir)

    def _make_amalgamation(self, source_dir):
        self.announce("Running make", level=log.INFO)
        subprocess.check_call(["make", "sqlite3.c"], cwd=source_dir)


def get_setup_args():

    PYSQLITE_VERSION = None

    version_re = re.compile('#define PYSQLITE_VERSION "(.*)"')
    f = open(os.path.join("src", "module.h"))
    for line in f:
        match = version_re.match(line)
        if match:
            PYSQLITE_VERSION = match.groups()[0]
            PYSQLITE_MINOR_VERSION = ".".join(PYSQLITE_VERSION.split('.')[:2])
            break
    f.close()

    if not PYSQLITE_VERSION:
        print "Fatal error: PYSQLITE_VERSION could not be detected!"
        sys.exit(1)

    data_files = [("pysqlite2-doc",
                        glob.glob("doc/*.html") \
                      + glob.glob("doc/*.txt") \
                      + glob.glob("doc/*.css")),
                   ("pysqlite2-doc/code",
                        glob.glob("doc/code/*.py"))]

    py_modules = ["sqlite"]
    setup_args = dict(
            name = "pysqlite",
            version = PYSQLITE_VERSION,
            description = "DB-API 2.0 interface for SQLite 3.x",
            long_description=long_description,
            author = "Gerhard Haering",
            author_email = "gh@ghaering.de",
            license = "zlib/libpng license",
            platforms = "ALL",
            url = "https://bitbucket.org/bluehorn/pysqlite/overview",
            download_url = "https://bitbucket.org/bluehorn/pysqlite/",

            # Description of the modules and packages in the distribution
            package_dir = {"pysqlite2": "lib"},
            packages = ["pysqlite2", "pysqlite2.test"] +
                       (["pysqlite2.test.py25"], [])[sys.version_info < (2, 5)],
            scripts=[],
            data_files = data_files,

            ext_modules = [Extension( name="pysqlite2._sqlite",
                                      sources=sources,
                                      include_dirs=include_dirs,
                                      library_dirs=library_dirs,
                                      runtime_library_dirs=runtime_library_dirs,
                                      libraries=libraries,
                                      extra_objects=extra_objects,
                                      define_macros=define_macros
                                      )],
            classifiers = [
            "Development Status :: 5 - Production/Stable",
            "Intended Audience :: Developers",
            "License :: OSI Approved :: zlib/libpng License",
            "Operating System :: MacOS :: MacOS X",
            "Operating System :: Microsoft :: Windows",
            "Operating System :: POSIX",
            "Programming Language :: C",
            "Programming Language :: Python",
            "Topic :: Database :: Database Engines/Servers",
            "Topic :: Software Development :: Libraries :: Python Modules"],
            cmdclass = {"build_docs": DocBuilder, "update_amalgamation": UpdateAmalgamation}
            )

    setup_args["cmdclass"].update({"build_docs": DocBuilder, "build_ext": MyBuildExt, "cross_bdist_wininst": cross_bdist_wininst.bdist_wininst})
    return setup_args

def main():
    setup(**get_setup_args())

if __name__ == "__main__":
    main()
