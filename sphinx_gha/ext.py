import typing as ty

from docutils import nodes
from docutils.parsers import rst
from docutils.parsers.rst import directives
from docutils import statemachine
from sphinx import application
from sphinx.util import logging
from sphinx.util import nodes as sphinx_nodes
from sphinx.ext.autodoc import mock


class ActionDirective(rst.Directive):
    has_content = False
    required_arguments = 1
    option_spec = {
        'path': directives.unchanged_required,
    }

    def run(self):
        return []


def setup(app: application.Sphinx) -> ty.Dict[str, ty.Any]:
    app.add_directive("gh-action", ActionDirective)

    return {
        'parallel_read_safe': True,
        'parallel_write_safe': True,
    }
