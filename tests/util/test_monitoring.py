import mock
import pytest
from kafka.common import KafkaUnavailableError
from test_offsets import MyKafkaClient
from test_offsets import TestOffsetsBase

from yelp_kafka_tool.util.error import UnknownPartitions
from yelp_kafka_tool.util.error import UnknownTopic
from yelp_kafka_tool.util.monitoring import ConsumerPartitionOffsets
from yelp_kafka_tool.util.monitoring import get_consumer_offsets_metadata


class TestMonitoring(TestOffsetsBase):

    def test_offset_metadata_invalid_arguments(self, kafka_client_mock):
        with pytest.raises(TypeError):
            get_consumer_offsets_metadata(
                kafka_client_mock,
                "this won't even be consulted",
                "this should be a list or dict",
            )

    def test_offset_metadata_unknown_topic(self, kafka_client_mock):
        with pytest.raises(UnknownTopic):
            get_consumer_offsets_metadata(
                kafka_client_mock,
                "this won't even be consulted",
                ["something that doesn't exist"],
            )

    def test_offset_metadata_unknown_topic_no_fail(self, kafka_client_mock):
        actual = get_consumer_offsets_metadata(
            kafka_client_mock,
            "this won't even be consulted",
            ["something that doesn't exist"],
            raise_on_error=False
        )
        assert not actual

    def test_offset_metadata_unknown_partitions(self, kafka_client_mock):
        with pytest.raises(UnknownPartitions):
            get_consumer_offsets_metadata(
                kafka_client_mock,
                self.group,
                {'topic1': [99]},
            )

    def test_offset_metadata_unknown_partitions_no_fail(self, kafka_client_mock):
        actual = get_consumer_offsets_metadata(
            kafka_client_mock,
            self.group,
            {'topic1': [99]},
            raise_on_error=False
        )
        assert not actual

    def test_offset_metadata_invalid_partition_subset(self, kafka_client_mock):
        with pytest.raises(UnknownPartitions):
            get_consumer_offsets_metadata(
                kafka_client_mock,
                self.group,
                {'topic1': [1, 99]},
            )

    def test_offset_metadata_invalid_partition_subset_no_fail(
        self,
        kafka_client_mock
    ):
        # Partition 99 does not exist, so we expect to have
        # offset metadata ONLY for partition 1.
        expected = [
            ConsumerPartitionOffsets('topic1', 1, 20, 30, 5)
        ]

        actual = get_consumer_offsets_metadata(
            kafka_client_mock,
            self.group,
            {'topic1': [1, 99]},
            raise_on_error=False
        )
        assert 'topic1' in actual
        assert actual['topic1'] == expected

    def test_get_metadata_kafka_error(self, kafka_client_mock):
        with mock.patch.object(
            MyKafkaClient,
            'load_metadata_for_topics',
            side_effect=KafkaUnavailableError("Boom!"),
            autospec=True
        ) as mock_func:
            with pytest.raises(KafkaUnavailableError):
                get_consumer_offsets_metadata(
                    kafka_client_mock,
                    self.group,
                    {'topic1': [99]},
                )
            assert mock_func.call_count == 2
