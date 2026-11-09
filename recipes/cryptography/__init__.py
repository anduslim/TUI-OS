"""Local override of python-for-android's cryptography recipe.

p4a ``master`` (what Buildozer uses) builds cryptography's Rust extension
(``_rust.abi3.so``) without linking it to libpython. Android's loader then
fails on import with ``dlopen failed: cannot locate symbol "PyExc_TypeError"``,
which takes SSH (and so the Shell and K9s tabs) down with it.

p4a ``develop`` fixed this in RustCompiledComponentsRecipe ("Fix libpython
link", 2026-05-21) by adding ``-Clink-arg=-lpython<ver>`` to RUSTFLAGS. This
copy of the recipe does the same, so the build works on either branch. Drop
it once that fix reaches p4a master. tools/check_native_libs.sh fails the CI
build if the extension is ever missing the libpython link again.
"""

from os.path import join

from pythonforandroid.recipe import RustCompiledComponentsRecipe


class CryptographyRecipe(RustCompiledComponentsRecipe):

    name = "cryptography"
    version = "46.0.3"
    url = "https://github.com/pyca/cryptography/archive/refs/tags/{version}.tar.gz"
    depends = ["openssl", "cffi"]

    def get_recipe_env(self, arch, **kwargs):
        env = super().get_recipe_env(arch, **kwargs)
        openssl_build_dir = self.get_recipe("openssl", self.ctx).get_build_dir(arch.arch)
        build_target = self.RUST_ARCH_CODES[arch.arch].upper().replace("-", "_")
        env[f"{build_target}_OPENSSL_INCLUDE_DIR"] = join(openssl_build_dir, "include")
        env[f"{build_target}_OPENSSL_LIB_DIR"] = join(openssl_build_dir)
        link = f"-Clink-arg=-lpython{self.ctx.python_recipe.link_version}"
        if link not in env.get("RUSTFLAGS", ""):
            env["RUSTFLAGS"] = (env.get("RUSTFLAGS", "") + " " + link).strip()
        return env


recipe = CryptographyRecipe()
