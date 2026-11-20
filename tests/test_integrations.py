import unittest

from xmux_multiplexer.integrations import (
    IntegrationCategory, IntegrationDescriptor, Operation, OperationSupport,
    SecretReference, SupportState, TransportKind, supports,
)


class IntegrationContractTests(unittest.TestCase):
    def test_declared_and_undeclared_operations_are_explicit(self):
        descriptor = IntegrationDescriptor(
            "local-mux", "Local mux", IntegrationCategory.CODING_MUX,
            frozenset({TransportKind.CLI}),
            (OperationSupport(Operation.SUBMIT_TASK, SupportState.SUPPORTED),
             OperationSupport(Operation.CANCEL_TASK, SupportState.UNSUPPORTED,
                              "mux_has_no_cancel")),
            secret_refs=(SecretReference("secrets/local-mux"),),
        )
        self.assertTrue(supports(descriptor, Operation.SUBMIT_TASK))
        self.assertFalse(supports(descriptor, Operation.CANCEL_TASK))
        self.assertEqual(descriptor.support_for(Operation.GET_RESULT).reason,
                         "operation_not_declared")
        self.assertEqual(descriptor.secret_refs[0].reference, "secrets/local-mux")


if __name__ == "__main__":
    unittest.main()
