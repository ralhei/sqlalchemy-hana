# Copyright 2015 SAP SE.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http: //www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND,
# either express or implied. See the License for the specific
# language governing permissions and limitations under the License.

from sqlalchemy import sql, types, util
from sqlalchemy.engine import default
from sqlalchemy.sql import compiler

from sqlalchemy_hana import types as hana_types

import pyhdb

class HANAIdentifierPreparer(compiler.IdentifierPreparer):

    def format_constraint(self, constraint):
        """HANA doesn't support named constraint"""
        return None

class HANAStatementCompiler(compiler.SQLCompiler):

    def visit_sequence(self, seq):
        return self.dialect.identifier_preparer.format_sequence(seq) \
               + ".NEXTVAL"

    def default_from(self):
        return " FROM DUMMY"

class HANATypeCompiler(compiler.GenericTypeCompiler):

    def visit_boolean(self, type_):
        return self.visit_TINYINT(type_)

    def visit_NUMERIC(self, type_):
        return self.visit_DECIMAL(type_)

    def visit_TINYINT(self, type_):
        return "TINYINT"

    def visit_DOUBLE(self, type_):
        return "DOUBLE"

class HANADDLCompiler(compiler.DDLCompiler):

    def visit_check_constraint(self, constraint):
        """HANA doesn't support check constraints."""
        return None

    def visit_unique_constraint(self, constraint):
        if len(constraint) == 0:
            return ''

        text = ""
        if constraint.name is not None:
            formatted_name = self.preparer.format_constraint(constraint)
            if formatted_name is not None:
                text += "CONSTRAINT %s " % formatted_name
        text += "UNIQUE (%s)" % (
                ', '.join(self.preparer.quote(c.name)
                          for c in constraint))
        text += self.define_constraint_deferrability(constraint)
        return text

class HANAExecutionContext(default.DefaultExecutionContext):

    def fire_sequence(self, seq, type_):
        seq = self.dialect.identifier_preparer.format_sequence(seq)
        return self._execute_scalar(
            "SELECT %s.NEXTVAL FROM DUMMY" % seq,
            type_
        )

class HANADialect(default.DefaultDialect):

    name = "hana"
    driver = 'pyhdb'

    statement_compiler = HANAStatementCompiler
    type_compiler = HANATypeCompiler
    ddl_compiler = HANADDLCompiler
    preparer = HANAIdentifierPreparer
    execution_ctx_cls = HANAExecutionContext

    encoding = "cesu-8"
    convert_unicode = True
    supports_unicode_statements = True
    supports_unicode_binds = True
    requires_name_normalize = True

    supports_sequences = True
    supports_native_decimal = True

    ischema_names = {}
    colspecs = {
        types.Boolean: hana_types.BOOLEAN,
        types.Date: hana_types.DATE,
        types.Time: hana_types.TIME,
        types.DateTime: hana_types.TIMESTAMP,
    }

    postfetch_lastrowid = False
    implicit_returning = False
    supports_empty_insert = False
    supports_native_boolean = False
    supports_default_values = False

    # pyhdb
    supports_sane_multi_rowcount = False

    @classmethod
    def dbapi(cls):
        return pyhdb

    def is_disconnect(self, error, connection, cursor):
        return connection.closed

    def on_connect(self):
        return None

    def create_connect_args(self, url):
        kwargs = url.translate_connect_args(username="user")
        kwargs.setdefault("port", 30015)
        return (), kwargs

    def _get_server_version_info(self, connection):
        pass

    def _get_default_schema_name(self, connection):
        return connection.engine.url.username.upper()

    def _check_unicode_returns(self, connection):
        return True

    def _check_unicode_description(self, connection):
        return True

    def normalize_name(self, name):
        if name is None:
            return None

        if name.upper() == name and not \
           self.identifier_preparer._requires_quotes(name.lower()):
            name = name.lower()

        return name

    def denormalize_name(self, name):
        if name is None:
            return None

        if name.lower() == name and not \
           self.identifier_preparer._requires_quotes(name.lower()):
            name = name.upper()
        return name

    def has_table(self, connection, table_name, schema=None):
        schema = schema or self.default_schema_name

        result = connection.execute(
            sql.text(
                "SELECT 1 FROM TABLES "
                "WHERE SCHEMA_NAME=:schema AND TABLE_NAME=:table",
            ).bindparams(
                schema=unicode(self.denormalize_name(schema)),
                table=unicode(self.denormalize_name(table_name))
            )
        )
        return bool(result.first())

    def has_sequence(self, connection, sequence_name, schema=None):
        schema = schema or self.default_schema_name
        result = connection.execute(
            sql.text(
                "SELECT 1 FROM SEQUENCES "
                "WHERE SCHEMA_NAME=:schema AND SEQUENCE_NAME=:sequence",
            ).bindparams(
                schema=unicode(self.denormalize_name(schema)),
                sequence=unicode(self.denormalize_name(sequence_name))
            )
        )
        return bool(result.first())

    def get_schema_names(self, connection, **kwargs):
        result = connection.execute(
            sql.text("SELECT SCHEMA_NAME FROM SCHEMAS")
        )

        return list([
            self.normalize_name(name) for name, in result.fetchall()
        ])

    def get_table_names(self, connection, schema=None, **kwargs):
        schema = schema or self.default_schema_name

        result = connection.execute(
            sql.text(
                "SELECT TABLE_NAME FROM TABLES WHERE SCHEMA_NAME=:schema",
            ).bindparams(
                schema=unicode(self.denormalize_name(schema)),
            )
        )

        tables = list([
            self.normalize_name(row[0]) for row in result.fetchall()
        ])
        return tables

    def get_columns(self, connection, table_name, schema=None, **kwargs):
        schema = schema or self.default_schema_name

        result = connection.execute(
            sql.text(
                "SELECT COLUMN_NAME, DATA_TYPE_NAME, DEFAULT_VALUE, "
                "IS_NULLABLE, LENGTH, SCALE FROM TABLE_COLUMNS "
                "WHERE SCHEMA_NAME=:schema AND TABLE_NAME=:table "
                "ORDER BY POSITION"
            ).bindparams(
                schema=unicode(self.denormalize_name(schema)),
                table=unicode(self.denormalize_name(table_name))
            )
        )

        columns = []
        for row in result.fetchall():
            column = {
                'name': self.normalize_name(row[0]),
                'default': row[2],
                'nullable': row[3] == "TRUE"
            }

            if hasattr(types, row[1]):
                column['type'] = getattr(types, row[1])
            elif hasattr(hana_types, row[1]):
                column['type'] = getattr(hana_types, row[1])
            else:
                util.warn("Did not recognize type '%s' of column '%s'" % (
                    row[1], column['name']
                ))
                column['type'] = types.NULLTYPE

            if column['type'] == types.DECIMAL:
                column['type'] = types.DECIMAL(row[4], row[5])
            elif column['type'] == types.VARCHAR:
                column['type'] = types.VARCHAR(row[4])

            columns.append(column)

        return columns

    def get_foreign_keys(self, connection, table_name, schema=None, **kwargs):
        lookup_schema = schema or self.default_schema_name

        result = connection.execute(
            sql.text(
                "SELECT COLUMN_NAME, REFERENCED_SCHEMA_NAME, "
                "REFERENCED_TABLE_NAME,  REFERENCED_COLUMN_NAME "
                "FROM REFERENTIAL_CONSTRAINTS "
                "WHERE SCHEMA_NAME=:schema AND TABLE_NAME=:table "
                "ORDER BY CONSTRAINT_NAME, POSITION"
            ).bindparams(
                schema=unicode(self.denormalize_name(lookup_schema)),
                table=unicode(self.denormalize_name(table_name))
            )
        )

        foreign_keys = []
        for row in result:
            foreign_key = {
                "name": None, # No named foreign key support
                "constrained_columns": [self.normalize_name(row[0]),],
                "referred_schema": None,
                "referred_table": self.normalize_name(row[2]),
                "referred_columns": [self.normalize_name(row[3]),],
            }

            if schema is not None or row[1] != self.default_schema_name:
               foreign_key["referred_schema"] = row[1]

            foreign_keys.append(foreign_key)
        return foreign_keys

    def get_indexes(self, connection, table_name, schema=None, **kwargs):
        schema = schema or self.default_schema_name

        result = connection.execute(
            sql.text(
                'SELECT "INDEX_NAME", "COLUMN_NAME", "CONSTRAINT" '
                "FROM INDEX_COLUMNS "
                "WHERE SCHEMA_NAME=:schema AND TABLE_NAME=:table "
                "ORDER BY POSITION"
            ).bindparams(
                schema=unicode(self.denormalize_name(schema)),
                table=unicode(self.denormalize_name(table_name))
            )
        )

        indexes = {}
        for name, column, constraint in result.fetchall():
            if name.startswith("_SYS"):
                continue

            name = self.normalize_name(name)
            column = self.normalize_name(column)

            if name not in indexes:
                indexes[name] = {
                    "name": name,
                    "unique": False,
                    "column_names": [column,]
                }

                if constraint is not None:
                    indexes[name]["unique"] = "UNIQUE" in constraint.upper()

            else:
                indexes[name]["column_names"].append(column)

        return indexes.values()

    def get_pk_constraint(self, connection, table_name, schema=None, **kwargs):
        schema = schema or self.default_schema_name

        result = connection.execute(
            sql.text(
                "SELECT CONSTRAINT_NAME, COLUMN_NAME FROM CONSTRAINTS "
                "WHERE SCHEMA_NAME=:schema AND TABLE_NAME=:table AND "
                "IS_PRIMARY_KEY='TRUE' "
                "ORDER BY POSITION"
            ).bindparams(
                schema=unicode(self.denormalize_name(schema)),
                table=unicode(self.denormalize_name(table_name))
            )
        )

        constraint_name = None
        constrained_columns = []
        for row in result.fetchall():
            constraint_name = row[0]
            constrained_columns.append(self.normalize_name(row[1]))

        return {
            "name": self.normalize_name(constraint_name),
            "constrained_columns": constrained_columns
        }

    # def get_unique_constraints(self, connection, table_name, schema=None,
    #                            **kwargs):
    #     schema = schema or self.default_schema_name

    #     result = connection.execute(
    #         sql.text(
    #             "SELECT CONSTRAINT_NAME, COLUMN_NAME FROM CONSTRAINTS "
    #             "WHERE SCHEMA_NAME=:schema AND TABLE_NAME=:table "
    #             "ORDER BY CONSTRAINT_NAME, POSITION"
    #         ).bindparams(
    #             schema=unicode(self.denormalize_name(schema)),
    #             table=unicode(self.denormalize_name(table_name))
    #         )
    #     )

    #     constraints_order = []
    #     constraints_columns = {}
    #     for constraint, column_name in result.fetchall():
    #         if constraint not in constraints_columns:
    #             constraints_order.append(constraint)
    #             constraints_columns[constraint] = [column_name,]
    #         else:
    #             constraints_columns[constraint].append(column_name)

    #     constraints = []
    #     for constraint in constraints_order:
    #         constraints.append({
    #             'name': constraint,
    #             'column_names': constraints_columns[constraint]
    #         })
    #     return constraints
