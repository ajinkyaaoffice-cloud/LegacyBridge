      *================================================================*
      * ACCTREC - INTERNAL ACCOUNT MASTER RECORD                       *
      * BALANCE IS PACKED DECIMAL. LOAN BALANCES ARE NEGATIVE (OWED).  *
      *================================================================*
       01  ACCT-REC.
           05 ACCT-ID              PIC X(8).
           05 ACCT-NAME            PIC X(20).
           05 ACCT-TYPE            PIC X.
              88 ACCT-CHECKING     VALUE 'C'.
              88 ACCT-SAVINGS      VALUE 'S'.
              88 ACCT-LOAN         VALUE 'L'.
           05 ACCT-BALANCE         PIC S9(9)V99 COMP-3.
           05 ACCT-RATE            PIC 9V9(4) COMP-3.
           05 ACCT-OPEN-DATE       PIC 9(8).
           05 ACCT-STATUS          PIC X.
              88 ACCT-ACTIVE       VALUE 'A'.
              88 ACCT-FROZEN       VALUE 'F'.
              88 ACCT-CLOSED       VALUE 'C'.
