package com.example.voicenote.data;

import android.database.Cursor;
import android.os.CancellationSignal;
import androidx.annotation.NonNull;
import androidx.annotation.Nullable;
import androidx.room.CoroutinesRoom;
import androidx.room.EntityDeletionOrUpdateAdapter;
import androidx.room.EntityInsertionAdapter;
import androidx.room.RoomDatabase;
import androidx.room.RoomSQLiteQuery;
import androidx.room.SharedSQLiteStatement;
import androidx.room.util.CursorUtil;
import androidx.room.util.DBUtil;
import androidx.sqlite.db.SupportSQLiteStatement;
import java.lang.Class;
import java.lang.Exception;
import java.lang.Long;
import java.lang.Object;
import java.lang.Override;
import java.lang.String;
import java.lang.SuppressWarnings;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.concurrent.Callable;
import javax.annotation.processing.Generated;
import kotlin.Unit;
import kotlin.coroutines.Continuation;
import kotlinx.coroutines.flow.Flow;

@Generated("androidx.room.RoomProcessor")
@SuppressWarnings({"unchecked", "deprecation"})
public final class NoteDao_Impl implements NoteDao {
  private final RoomDatabase __db;

  private final EntityInsertionAdapter<Note> __insertionAdapterOfNote;

  private final EntityDeletionOrUpdateAdapter<Note> __deletionAdapterOfNote;

  private final EntityDeletionOrUpdateAdapter<Note> __updateAdapterOfNote;

  private final SharedSQLiteStatement __preparedStmtOfMarkReminded;

  public NoteDao_Impl(@NonNull final RoomDatabase __db) {
    this.__db = __db;
    this.__insertionAdapterOfNote = new EntityInsertionAdapter<Note>(__db) {
      @Override
      @NonNull
      protected String createQuery() {
        return "INSERT OR ABORT INTO `notes` (`id`,`content`,`audioPath`,`category`,`eventTime`,`location`,`person`,`summary`,`topic`,`reminderAt`,`reminded`,`createdAt`) VALUES (nullif(?, 0),?,?,?,?,?,?,?,?,?,?,?)";
      }

      @Override
      protected void bind(@NonNull final SupportSQLiteStatement statement,
          @NonNull final Note entity) {
        statement.bindLong(1, entity.getId());
        statement.bindString(2, entity.getContent());
        if (entity.getAudioPath() == null) {
          statement.bindNull(3);
        } else {
          statement.bindString(3, entity.getAudioPath());
        }
        statement.bindString(4, entity.getCategory());
        if (entity.getEventTime() == null) {
          statement.bindNull(5);
        } else {
          statement.bindLong(5, entity.getEventTime());
        }
        if (entity.getLocation() == null) {
          statement.bindNull(6);
        } else {
          statement.bindString(6, entity.getLocation());
        }
        if (entity.getPerson() == null) {
          statement.bindNull(7);
        } else {
          statement.bindString(7, entity.getPerson());
        }
        if (entity.getSummary() == null) {
          statement.bindNull(8);
        } else {
          statement.bindString(8, entity.getSummary());
        }
        if (entity.getTopic() == null) {
          statement.bindNull(9);
        } else {
          statement.bindString(9, entity.getTopic());
        }
        if (entity.getReminderAt() == null) {
          statement.bindNull(10);
        } else {
          statement.bindLong(10, entity.getReminderAt());
        }
        final int _tmp = entity.getReminded() ? 1 : 0;
        statement.bindLong(11, _tmp);
        statement.bindLong(12, entity.getCreatedAt());
      }
    };
    this.__deletionAdapterOfNote = new EntityDeletionOrUpdateAdapter<Note>(__db) {
      @Override
      @NonNull
      protected String createQuery() {
        return "DELETE FROM `notes` WHERE `id` = ?";
      }

      @Override
      protected void bind(@NonNull final SupportSQLiteStatement statement,
          @NonNull final Note entity) {
        statement.bindLong(1, entity.getId());
      }
    };
    this.__updateAdapterOfNote = new EntityDeletionOrUpdateAdapter<Note>(__db) {
      @Override
      @NonNull
      protected String createQuery() {
        return "UPDATE OR ABORT `notes` SET `id` = ?,`content` = ?,`audioPath` = ?,`category` = ?,`eventTime` = ?,`location` = ?,`person` = ?,`summary` = ?,`topic` = ?,`reminderAt` = ?,`reminded` = ?,`createdAt` = ? WHERE `id` = ?";
      }

      @Override
      protected void bind(@NonNull final SupportSQLiteStatement statement,
          @NonNull final Note entity) {
        statement.bindLong(1, entity.getId());
        statement.bindString(2, entity.getContent());
        if (entity.getAudioPath() == null) {
          statement.bindNull(3);
        } else {
          statement.bindString(3, entity.getAudioPath());
        }
        statement.bindString(4, entity.getCategory());
        if (entity.getEventTime() == null) {
          statement.bindNull(5);
        } else {
          statement.bindLong(5, entity.getEventTime());
        }
        if (entity.getLocation() == null) {
          statement.bindNull(6);
        } else {
          statement.bindString(6, entity.getLocation());
        }
        if (entity.getPerson() == null) {
          statement.bindNull(7);
        } else {
          statement.bindString(7, entity.getPerson());
        }
        if (entity.getSummary() == null) {
          statement.bindNull(8);
        } else {
          statement.bindString(8, entity.getSummary());
        }
        if (entity.getTopic() == null) {
          statement.bindNull(9);
        } else {
          statement.bindString(9, entity.getTopic());
        }
        if (entity.getReminderAt() == null) {
          statement.bindNull(10);
        } else {
          statement.bindLong(10, entity.getReminderAt());
        }
        final int _tmp = entity.getReminded() ? 1 : 0;
        statement.bindLong(11, _tmp);
        statement.bindLong(12, entity.getCreatedAt());
        statement.bindLong(13, entity.getId());
      }
    };
    this.__preparedStmtOfMarkReminded = new SharedSQLiteStatement(__db) {
      @Override
      @NonNull
      public String createQuery() {
        final String _query = "UPDATE notes SET reminded = 1 WHERE id = ?";
        return _query;
      }
    };
  }

  @Override
  public Object insert(final Note note, final Continuation<? super Long> $completion) {
    return CoroutinesRoom.execute(__db, true, new Callable<Long>() {
      @Override
      @NonNull
      public Long call() throws Exception {
        __db.beginTransaction();
        try {
          final Long _result = __insertionAdapterOfNote.insertAndReturnId(note);
          __db.setTransactionSuccessful();
          return _result;
        } finally {
          __db.endTransaction();
        }
      }
    }, $completion);
  }

  @Override
  public Object delete(final Note note, final Continuation<? super Unit> $completion) {
    return CoroutinesRoom.execute(__db, true, new Callable<Unit>() {
      @Override
      @NonNull
      public Unit call() throws Exception {
        __db.beginTransaction();
        try {
          __deletionAdapterOfNote.handle(note);
          __db.setTransactionSuccessful();
          return Unit.INSTANCE;
        } finally {
          __db.endTransaction();
        }
      }
    }, $completion);
  }

  @Override
  public Object update(final Note note, final Continuation<? super Unit> $completion) {
    return CoroutinesRoom.execute(__db, true, new Callable<Unit>() {
      @Override
      @NonNull
      public Unit call() throws Exception {
        __db.beginTransaction();
        try {
          __updateAdapterOfNote.handle(note);
          __db.setTransactionSuccessful();
          return Unit.INSTANCE;
        } finally {
          __db.endTransaction();
        }
      }
    }, $completion);
  }

  @Override
  public Object markReminded(final long id, final Continuation<? super Unit> $completion) {
    return CoroutinesRoom.execute(__db, true, new Callable<Unit>() {
      @Override
      @NonNull
      public Unit call() throws Exception {
        final SupportSQLiteStatement _stmt = __preparedStmtOfMarkReminded.acquire();
        int _argIndex = 1;
        _stmt.bindLong(_argIndex, id);
        try {
          __db.beginTransaction();
          try {
            _stmt.executeUpdateDelete();
            __db.setTransactionSuccessful();
            return Unit.INSTANCE;
          } finally {
            __db.endTransaction();
          }
        } finally {
          __preparedStmtOfMarkReminded.release(_stmt);
        }
      }
    }, $completion);
  }

  @Override
  public Object getById(final long id, final Continuation<? super Note> $completion) {
    final String _sql = "SELECT * FROM notes WHERE id = ?";
    final RoomSQLiteQuery _statement = RoomSQLiteQuery.acquire(_sql, 1);
    int _argIndex = 1;
    _statement.bindLong(_argIndex, id);
    final CancellationSignal _cancellationSignal = DBUtil.createCancellationSignal();
    return CoroutinesRoom.execute(__db, false, _cancellationSignal, new Callable<Note>() {
      @Override
      @Nullable
      public Note call() throws Exception {
        final Cursor _cursor = DBUtil.query(__db, _statement, false, null);
        try {
          final int _cursorIndexOfId = CursorUtil.getColumnIndexOrThrow(_cursor, "id");
          final int _cursorIndexOfContent = CursorUtil.getColumnIndexOrThrow(_cursor, "content");
          final int _cursorIndexOfAudioPath = CursorUtil.getColumnIndexOrThrow(_cursor, "audioPath");
          final int _cursorIndexOfCategory = CursorUtil.getColumnIndexOrThrow(_cursor, "category");
          final int _cursorIndexOfEventTime = CursorUtil.getColumnIndexOrThrow(_cursor, "eventTime");
          final int _cursorIndexOfLocation = CursorUtil.getColumnIndexOrThrow(_cursor, "location");
          final int _cursorIndexOfPerson = CursorUtil.getColumnIndexOrThrow(_cursor, "person");
          final int _cursorIndexOfSummary = CursorUtil.getColumnIndexOrThrow(_cursor, "summary");
          final int _cursorIndexOfTopic = CursorUtil.getColumnIndexOrThrow(_cursor, "topic");
          final int _cursorIndexOfReminderAt = CursorUtil.getColumnIndexOrThrow(_cursor, "reminderAt");
          final int _cursorIndexOfReminded = CursorUtil.getColumnIndexOrThrow(_cursor, "reminded");
          final int _cursorIndexOfCreatedAt = CursorUtil.getColumnIndexOrThrow(_cursor, "createdAt");
          final Note _result;
          if (_cursor.moveToFirst()) {
            final long _tmpId;
            _tmpId = _cursor.getLong(_cursorIndexOfId);
            final String _tmpContent;
            _tmpContent = _cursor.getString(_cursorIndexOfContent);
            final String _tmpAudioPath;
            if (_cursor.isNull(_cursorIndexOfAudioPath)) {
              _tmpAudioPath = null;
            } else {
              _tmpAudioPath = _cursor.getString(_cursorIndexOfAudioPath);
            }
            final String _tmpCategory;
            _tmpCategory = _cursor.getString(_cursorIndexOfCategory);
            final Long _tmpEventTime;
            if (_cursor.isNull(_cursorIndexOfEventTime)) {
              _tmpEventTime = null;
            } else {
              _tmpEventTime = _cursor.getLong(_cursorIndexOfEventTime);
            }
            final String _tmpLocation;
            if (_cursor.isNull(_cursorIndexOfLocation)) {
              _tmpLocation = null;
            } else {
              _tmpLocation = _cursor.getString(_cursorIndexOfLocation);
            }
            final String _tmpPerson;
            if (_cursor.isNull(_cursorIndexOfPerson)) {
              _tmpPerson = null;
            } else {
              _tmpPerson = _cursor.getString(_cursorIndexOfPerson);
            }
            final String _tmpSummary;
            if (_cursor.isNull(_cursorIndexOfSummary)) {
              _tmpSummary = null;
            } else {
              _tmpSummary = _cursor.getString(_cursorIndexOfSummary);
            }
            final String _tmpTopic;
            if (_cursor.isNull(_cursorIndexOfTopic)) {
              _tmpTopic = null;
            } else {
              _tmpTopic = _cursor.getString(_cursorIndexOfTopic);
            }
            final Long _tmpReminderAt;
            if (_cursor.isNull(_cursorIndexOfReminderAt)) {
              _tmpReminderAt = null;
            } else {
              _tmpReminderAt = _cursor.getLong(_cursorIndexOfReminderAt);
            }
            final boolean _tmpReminded;
            final int _tmp;
            _tmp = _cursor.getInt(_cursorIndexOfReminded);
            _tmpReminded = _tmp != 0;
            final long _tmpCreatedAt;
            _tmpCreatedAt = _cursor.getLong(_cursorIndexOfCreatedAt);
            _result = new Note(_tmpId,_tmpContent,_tmpAudioPath,_tmpCategory,_tmpEventTime,_tmpLocation,_tmpPerson,_tmpSummary,_tmpTopic,_tmpReminderAt,_tmpReminded,_tmpCreatedAt);
          } else {
            _result = null;
          }
          return _result;
        } finally {
          _cursor.close();
          _statement.release();
        }
      }
    }, $completion);
  }

  @Override
  public Flow<List<Note>> observeByCategory(final String category) {
    final String _sql = "SELECT * FROM notes WHERE category = ? ORDER BY createdAt DESC";
    final RoomSQLiteQuery _statement = RoomSQLiteQuery.acquire(_sql, 1);
    int _argIndex = 1;
    _statement.bindString(_argIndex, category);
    return CoroutinesRoom.createFlow(__db, false, new String[] {"notes"}, new Callable<List<Note>>() {
      @Override
      @NonNull
      public List<Note> call() throws Exception {
        final Cursor _cursor = DBUtil.query(__db, _statement, false, null);
        try {
          final int _cursorIndexOfId = CursorUtil.getColumnIndexOrThrow(_cursor, "id");
          final int _cursorIndexOfContent = CursorUtil.getColumnIndexOrThrow(_cursor, "content");
          final int _cursorIndexOfAudioPath = CursorUtil.getColumnIndexOrThrow(_cursor, "audioPath");
          final int _cursorIndexOfCategory = CursorUtil.getColumnIndexOrThrow(_cursor, "category");
          final int _cursorIndexOfEventTime = CursorUtil.getColumnIndexOrThrow(_cursor, "eventTime");
          final int _cursorIndexOfLocation = CursorUtil.getColumnIndexOrThrow(_cursor, "location");
          final int _cursorIndexOfPerson = CursorUtil.getColumnIndexOrThrow(_cursor, "person");
          final int _cursorIndexOfSummary = CursorUtil.getColumnIndexOrThrow(_cursor, "summary");
          final int _cursorIndexOfTopic = CursorUtil.getColumnIndexOrThrow(_cursor, "topic");
          final int _cursorIndexOfReminderAt = CursorUtil.getColumnIndexOrThrow(_cursor, "reminderAt");
          final int _cursorIndexOfReminded = CursorUtil.getColumnIndexOrThrow(_cursor, "reminded");
          final int _cursorIndexOfCreatedAt = CursorUtil.getColumnIndexOrThrow(_cursor, "createdAt");
          final List<Note> _result = new ArrayList<Note>(_cursor.getCount());
          while (_cursor.moveToNext()) {
            final Note _item;
            final long _tmpId;
            _tmpId = _cursor.getLong(_cursorIndexOfId);
            final String _tmpContent;
            _tmpContent = _cursor.getString(_cursorIndexOfContent);
            final String _tmpAudioPath;
            if (_cursor.isNull(_cursorIndexOfAudioPath)) {
              _tmpAudioPath = null;
            } else {
              _tmpAudioPath = _cursor.getString(_cursorIndexOfAudioPath);
            }
            final String _tmpCategory;
            _tmpCategory = _cursor.getString(_cursorIndexOfCategory);
            final Long _tmpEventTime;
            if (_cursor.isNull(_cursorIndexOfEventTime)) {
              _tmpEventTime = null;
            } else {
              _tmpEventTime = _cursor.getLong(_cursorIndexOfEventTime);
            }
            final String _tmpLocation;
            if (_cursor.isNull(_cursorIndexOfLocation)) {
              _tmpLocation = null;
            } else {
              _tmpLocation = _cursor.getString(_cursorIndexOfLocation);
            }
            final String _tmpPerson;
            if (_cursor.isNull(_cursorIndexOfPerson)) {
              _tmpPerson = null;
            } else {
              _tmpPerson = _cursor.getString(_cursorIndexOfPerson);
            }
            final String _tmpSummary;
            if (_cursor.isNull(_cursorIndexOfSummary)) {
              _tmpSummary = null;
            } else {
              _tmpSummary = _cursor.getString(_cursorIndexOfSummary);
            }
            final String _tmpTopic;
            if (_cursor.isNull(_cursorIndexOfTopic)) {
              _tmpTopic = null;
            } else {
              _tmpTopic = _cursor.getString(_cursorIndexOfTopic);
            }
            final Long _tmpReminderAt;
            if (_cursor.isNull(_cursorIndexOfReminderAt)) {
              _tmpReminderAt = null;
            } else {
              _tmpReminderAt = _cursor.getLong(_cursorIndexOfReminderAt);
            }
            final boolean _tmpReminded;
            final int _tmp;
            _tmp = _cursor.getInt(_cursorIndexOfReminded);
            _tmpReminded = _tmp != 0;
            final long _tmpCreatedAt;
            _tmpCreatedAt = _cursor.getLong(_cursorIndexOfCreatedAt);
            _item = new Note(_tmpId,_tmpContent,_tmpAudioPath,_tmpCategory,_tmpEventTime,_tmpLocation,_tmpPerson,_tmpSummary,_tmpTopic,_tmpReminderAt,_tmpReminded,_tmpCreatedAt);
            _result.add(_item);
          }
          return _result;
        } finally {
          _cursor.close();
        }
      }

      @Override
      protected void finalize() {
        _statement.release();
      }
    });
  }

  @Override
  public Object findMissedReminders(final long now,
      final Continuation<? super List<Note>> $completion) {
    final String _sql = "SELECT * FROM notes WHERE reminderAt IS NOT NULL AND reminded = 0 AND reminderAt < ?";
    final RoomSQLiteQuery _statement = RoomSQLiteQuery.acquire(_sql, 1);
    int _argIndex = 1;
    _statement.bindLong(_argIndex, now);
    final CancellationSignal _cancellationSignal = DBUtil.createCancellationSignal();
    return CoroutinesRoom.execute(__db, false, _cancellationSignal, new Callable<List<Note>>() {
      @Override
      @NonNull
      public List<Note> call() throws Exception {
        final Cursor _cursor = DBUtil.query(__db, _statement, false, null);
        try {
          final int _cursorIndexOfId = CursorUtil.getColumnIndexOrThrow(_cursor, "id");
          final int _cursorIndexOfContent = CursorUtil.getColumnIndexOrThrow(_cursor, "content");
          final int _cursorIndexOfAudioPath = CursorUtil.getColumnIndexOrThrow(_cursor, "audioPath");
          final int _cursorIndexOfCategory = CursorUtil.getColumnIndexOrThrow(_cursor, "category");
          final int _cursorIndexOfEventTime = CursorUtil.getColumnIndexOrThrow(_cursor, "eventTime");
          final int _cursorIndexOfLocation = CursorUtil.getColumnIndexOrThrow(_cursor, "location");
          final int _cursorIndexOfPerson = CursorUtil.getColumnIndexOrThrow(_cursor, "person");
          final int _cursorIndexOfSummary = CursorUtil.getColumnIndexOrThrow(_cursor, "summary");
          final int _cursorIndexOfTopic = CursorUtil.getColumnIndexOrThrow(_cursor, "topic");
          final int _cursorIndexOfReminderAt = CursorUtil.getColumnIndexOrThrow(_cursor, "reminderAt");
          final int _cursorIndexOfReminded = CursorUtil.getColumnIndexOrThrow(_cursor, "reminded");
          final int _cursorIndexOfCreatedAt = CursorUtil.getColumnIndexOrThrow(_cursor, "createdAt");
          final List<Note> _result = new ArrayList<Note>(_cursor.getCount());
          while (_cursor.moveToNext()) {
            final Note _item;
            final long _tmpId;
            _tmpId = _cursor.getLong(_cursorIndexOfId);
            final String _tmpContent;
            _tmpContent = _cursor.getString(_cursorIndexOfContent);
            final String _tmpAudioPath;
            if (_cursor.isNull(_cursorIndexOfAudioPath)) {
              _tmpAudioPath = null;
            } else {
              _tmpAudioPath = _cursor.getString(_cursorIndexOfAudioPath);
            }
            final String _tmpCategory;
            _tmpCategory = _cursor.getString(_cursorIndexOfCategory);
            final Long _tmpEventTime;
            if (_cursor.isNull(_cursorIndexOfEventTime)) {
              _tmpEventTime = null;
            } else {
              _tmpEventTime = _cursor.getLong(_cursorIndexOfEventTime);
            }
            final String _tmpLocation;
            if (_cursor.isNull(_cursorIndexOfLocation)) {
              _tmpLocation = null;
            } else {
              _tmpLocation = _cursor.getString(_cursorIndexOfLocation);
            }
            final String _tmpPerson;
            if (_cursor.isNull(_cursorIndexOfPerson)) {
              _tmpPerson = null;
            } else {
              _tmpPerson = _cursor.getString(_cursorIndexOfPerson);
            }
            final String _tmpSummary;
            if (_cursor.isNull(_cursorIndexOfSummary)) {
              _tmpSummary = null;
            } else {
              _tmpSummary = _cursor.getString(_cursorIndexOfSummary);
            }
            final String _tmpTopic;
            if (_cursor.isNull(_cursorIndexOfTopic)) {
              _tmpTopic = null;
            } else {
              _tmpTopic = _cursor.getString(_cursorIndexOfTopic);
            }
            final Long _tmpReminderAt;
            if (_cursor.isNull(_cursorIndexOfReminderAt)) {
              _tmpReminderAt = null;
            } else {
              _tmpReminderAt = _cursor.getLong(_cursorIndexOfReminderAt);
            }
            final boolean _tmpReminded;
            final int _tmp;
            _tmp = _cursor.getInt(_cursorIndexOfReminded);
            _tmpReminded = _tmp != 0;
            final long _tmpCreatedAt;
            _tmpCreatedAt = _cursor.getLong(_cursorIndexOfCreatedAt);
            _item = new Note(_tmpId,_tmpContent,_tmpAudioPath,_tmpCategory,_tmpEventTime,_tmpLocation,_tmpPerson,_tmpSummary,_tmpTopic,_tmpReminderAt,_tmpReminded,_tmpCreatedAt);
            _result.add(_item);
          }
          return _result;
        } finally {
          _cursor.close();
          _statement.release();
        }
      }
    }, $completion);
  }

  @NonNull
  public static List<Class<?>> getRequiredConverters() {
    return Collections.emptyList();
  }
}
